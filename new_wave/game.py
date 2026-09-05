"""Renderer-independent driving, streaming, camera, input and evidence collection."""
import argparse
from collections import deque
from dataclasses import asdict
import json
import math
from pathlib import Path
import resource
import sys
import time

import glfw
import numpy as np
from .vehicle import Vehicle, VehicleConfig, Input
from .world import World, WorldConfig
from .meshes import prop_meshes, instances_for_chunk
from .render_math import normalize
from .traffic import Traffic
from .recovery import clear_position

ROOT=Path(__file__).resolve().parents[1]
PRESETS={
    'Performance':dict(radius=3,resolution=16,shadow=1024,density=.65),
    'Balanced':dict(radius=4,resolution=24,shadow=2048,density=1.),
    'Quality':dict(radius=5,resolution=32,shadow=2048,density=1.35),
}


def parser():
    p=argparse.ArgumentParser(description='Roads / New Wave — continuous free driving on Metal or OpenGL')
    p.add_argument('--backend',choices=('auto','metal','opengl'),default='auto')
    p.add_argument('--config',type=Path,default=Path(__file__).with_name('config.json'))
    p.add_argument('--quality',choices=PRESETS)
    p.add_argument('--width',type=int);p.add_argument('--height',type=int)
    p.add_argument('--seed',type=int);p.add_argument('--radius',type=int)
    p.add_argument('--x',type=float,default=0);p.add_argument('--z',type=float,default=-35)
    p.add_argument('--yaw',type=float,default=0,help='initial heading in degrees')
    p.add_argument('--time',type=float,default=16,help='hour, 0–24')
    p.add_argument('--cycle',action='store_true',help='advance the day/night cycle')
    p.add_argument('--weather',choices=('clear','rain','storm'),default='clear')
    p.add_argument('--replay',type=Path,help='JSON list of {duration, keys} keyboard input segments')
    p.add_argument('--camera',choices=('chase','close','wide','road'),default='chase')
    p.add_argument('--frames',type=int,default=0,help='exit after N rendered frames')
    p.add_argument('--benchmark',type=float,default=0,help='automated drive duration in seconds after warmup')
    p.add_argument('--warmup',type=float,default=5,help='unmeasured warmup seconds')
    p.add_argument('--report',type=Path,help='write JSON performance and route evidence')
    p.add_argument('--screenshot',type=Path,help='save last frame')
    p.add_argument('--no-audio',action='store_true')
    p.add_argument('--no-music',action='store_true',help='keep ambience but mute the procedural score')
    p.add_argument('--uncapped',action='store_true',help='disable frame limiter and vsync; recorded in report')
    p.add_argument('--no-hud',action='store_true')
    p.add_argument('--reduced-motion',action='store_true')
    display=p.add_mutually_exclusive_group()
    display.add_argument('--fullscreen',action='store_true',dest='fullscreen',
                         help='use the primary display in fullscreen mode')
    display.add_argument('--windowed',action='store_false',dest='fullscreen',
                         help='run in a window (default for drive.py directly)')
    p.set_defaults(fullscreen=False)
    return p


def instance(x,y,z,yaw=0,scale=1,pitch=0,roll=0,material=0):
    return np.array([[x,y,z,yaw,scale,scale,scale,pitch,1,1,1,material+roll]],dtype=np.float32)


def meta_key_actions(pressed, paused):
    """Return (paused, quit); kept pure so exit behavior cannot regress."""
    if glfw.KEY_ESCAPE in pressed:
        return paused, True
    if glfw.KEY_P in pressed:
        paused=not paused
    return paused, glfw.KEY_Q in pressed and paused


class Camera:
    def __init__(self):self.position=None;self.target=None;self.anchor=None
    def update(self,state,world,dt,mode,reduced):
        pos=np.array([state.x,state.y,state.z])
        f=np.array([-math.sin(state.yaw),0,-math.cos(state.yaw)])
        side=np.array([math.cos(state.yaw),0,-math.sin(state.yaw)])
        if mode=='close':desired=pos-f*5+side*4+np.array([0,1.9,0]);target=pos+np.array([0,.1,0])
        elif mode=='wide':desired=pos-f*36+side*19+np.array([0,22,0]);target=pos+f*8
        elif mode=='road':desired=pos+f*1.8+np.array([0,.65,0]);target=pos+f*30+np.array([0,.4,0])
        else:
            distance=9.5+(0 if reduced else min(abs(state.speed)*.055,2.5))
            desired=pos-f*distance+np.array([0,4.1,0]);target=pos+f*10+np.array([0,1.15,0])
        desired[1]=max(desired[1],world.surface_height(desired[0],desired[2])+1.0)
        if self.position is None:self.position=desired;self.target=target
        elif self.anchor is not None:
            # Preserve chase distance at speed; smooth orientation and suspension,
            # rather than accumulating metres of translation lag behind the car.
            delta=pos-self.anchor
            self.position+=delta;self.target+=delta
        self.anchor=pos.copy()
        a=1-math.exp(-dt*(8 if reduced else 5))
        self.position+=(desired-self.position)*a
        self.target+=(target-self.target)*(1-math.exp(-dt*8))
        self.position[1]=max(self.position[1],world.surface_height(self.position[0],self.position[2])+.7)
        return self.position,self.target


def main(argv=None,audio_module=None):
    args=parser().parse_args(argv)
    replay=json.loads(args.replay.read_text()) if args.replay else None
    config=json.loads(args.config.read_text())
    quality=args.quality or config['quality'];preset=PRESETS[quality]
    width=args.width if args.width is not None else config['width']
    height=args.height if args.height is not None else config['height']
    if width<320 or height<240:raise ValueError('Resolution must be at least 320x240')
    world_cfg=dict(config.get('world',{}),seed=args.seed if args.seed is not None else config['seed'],radius=args.radius if args.radius is not None else preset['radius'],resolution=preset['resolution'])
    world_cfg['density']=world_cfg.get('density',1)*preset['density']
    world=World(WorldConfig(**world_cfg))
    backend=args.backend
    if backend=='auto':backend='metal' if sys.platform=='darwin' else 'opengl'
    if backend=='metal':
        from .metal_renderer import MetalRenderer as Renderer
    else:
        from .gl_renderer import GLRenderer as Renderer
    renderer=None;audio=None;music=None
    try:
        renderer=Renderer(width,height,'ROADS / NEW WAVE',vsync=not args.uncapped,shadow_size=preset['shadow'])
        if args.fullscreen:
            monitor=glfw.get_primary_monitor()
            mode=glfw.get_video_mode(monitor) if monitor else None
            if not monitor or not mode:
                raise RuntimeError('Primary display is unavailable for fullscreen mode')
            glfw.set_window_monitor(renderer.window,monitor,0,0,
                                    mode.size.width,mode.size.height,
                                    mode.refresh_rate)
            renderer.poll()
        display_size=glfw.get_framebuffer_size(renderer.window)
        renderer.info.update(fullscreen=args.fullscreen,
                             render_resolution=[width,height],
                             display_resolution=list(display_size))
        print('RENDERER '+json.dumps(renderer.info),flush=True)
        if (ROOT/'assets/new_wave/roadside.npz').exists():
            with np.load(ROOT/'assets/new_wave/roadside.npz') as library:
                meshes={key:library[key] for key in library.files}
        else:meshes=prop_meshes()
        for key,mesh in meshes.items():renderer.upload_mesh(key,mesh)
        with np.load(ROOT/'assets/new_wave/roamer.npz') as asset:
            renderer.upload_mesh('car_body',asset['body']);renderer.upload_mesh('car_wheel',asset['wheel'])
        car=Vehicle(VehicleConfig(**config.get('vehicle',{})),x=args.x,z=args.z,yaw=math.radians(args.yaw))
        car.recover(world.surface_height)
        # Prepare all initially visible chunks before starting the timed gameplay clock.
        origin=(math.floor(car.state.x/world.config.chunk_size)*world.config.chunk_size,math.floor(car.state.z/world.config.chunk_size)*world.config.chunk_size)
        chunk_instances={}
        def accept(chunk):
            renderer.upload_mesh(chunk.key,chunk.vertices)
            chunk_instances[chunk.key]=instances_for_chunk(chunk,origin)
        while True:
            world.update(car.state.x,car.state.z)
            for chunk in world.completed():accept(chunk)
            renderer.poll()
            if len(world.chunks)==(world.config.radius*2+1)**2:break
            if renderer.should_close():return
            time.sleep(.002)
        car.state.x,car.state.z=clear_position(world,car.state.x,car.state.z,prefer_road=False)
        car.recover(world.surface_height)
        traffic=Traffic(world,count=config.get('traffic_count',12),fog_distance=(world.config.radius-.4)*world.config.chunk_size)
        traffic.populate(car.state.x,car.state.z)
        if not args.no_audio and not args.benchmark:
            try:
                if audio_module is None:
                    import app as audio_module
                audio=audio_module.AmbientAudioMixer();audio.set_volumes(brown=.025,wind=.018);audio.start()
            except Exception as e:print(f'Ambient audio unavailable: {e}',file=sys.stderr)
            if not args.no_music:
                try:
                    if audio_module is None:
                        import app as audio_module
                    if not audio_module._FLUIDSYNTH_AVAILABLE:
                        raise RuntimeError('FluidSynth is unavailable; install it with brew install fluid-synth')
                    if not (ROOT/audio_module.SOUNDFONT_PATH).exists():
                        raise RuntimeError('SoundFont missing; run ./env/bin/python setup_soundfonts.py')
                    from .music import AdaptiveMusic
                    gain=float(config.get('music_volume',.22))
                    music=AdaptiveMusic(audio_module.MinimalEnsemblePlayer(
                        sf2_path=str(ROOT/audio_module.SOUNDFONT_PATH),gain=gain),gain)
                    music.start()
                except Exception as e:print(f'Music unavailable: {e}',file=sys.stderr)
        from .hud import render_hud
        camera=Camera();mode=args.camera;paused=False;show_help=True;previous_keys=set();frame=0
        controls=config.get('controls',{})
        bindings={k:getattr(glfw,'KEY_'+v) for k,v in controls.items()}
        last=time.perf_counter();start=last;sim_time=0.
        samples,physics_samples,stream_samples,render_samples,gpu_samples=(deque(maxlen=36000) for _ in range(5))
        route=deque(maxlen=600);peak_chunks=len(world.chunks);peak_pending=0;peak_meshes=len(renderer.meshes);hud=None;hud_due=0.;stats={};fps=60.;quit_requested=False
        cpu_start=time.process_time();notice='';notice_until=0;measured_dropped=None;input_events=deque(maxlen=64)
        while not renderer.should_close() and not quit_requested:
            now=time.perf_counter();dt=now-last;frame_interval=now-last;last=now;elapsed=now-start
            renderer.poll()
            keys={k for k in set(bindings.values())|{glfw.KEY_UP,glfw.KEY_DOWN,glfw.KEY_LEFT,glfw.KEY_RIGHT,glfw.KEY_ESCAPE,glfw.KEY_P,glfw.KEY_H,glfw.KEY_C,glfw.KEY_Q,glfw.KEY_F12,glfw.KEY_T} if glfw.get_key(renderer.window,k)==glfw.PRESS}
            if replay:
                cursor=0
                for segment in replay:
                    cursor+=segment['duration']
                    if elapsed<cursor:
                        keys.update(getattr(glfw,'KEY_'+k) for k in segment['keys']);break
            pressed=keys-previous_keys;previous_keys=keys
            if pressed or frame==0:
                input_events.append(dict(seconds=round(elapsed,3),keys=sorted(keys),speed=car.state.speed,x=car.state.x,z=car.state.z))
            old_paused=paused
            paused,requested=meta_key_actions(pressed,paused)
            quit_requested|=requested
            if paused!=old_paused:hud_due=0
            if glfw.KEY_H in pressed:show_help=not show_help;hud_due=0
            if glfw.KEY_C in pressed:
                modes=['chase','close','wide','road'];mode=modes[(modes.index(mode)+1)%4];camera=Camera()
            if glfw.KEY_T in pressed:args.time=(args.time+6)%24
            def held(name,extra=None):return bindings.get(name) in keys or extra in keys
            throttle=float(held('accelerate',glfw.KEY_UP))-float(held('reverse',glfw.KEY_DOWN))
            brake=float(held('brake'))
            control=Input(throttle=throttle,brake=brake,steer=float(held('left',glfw.KEY_LEFT))-float(held('right',glfw.KEY_RIGHT)),handbrake=held('handbrake'),recover=bindings.get('recover') in pressed)
            joystick=False
            for jid in range(glfw.JOYSTICK_1,glfw.JOYSTICK_LAST+1):
                if glfw.joystick_is_gamepad(jid):
                    pad=glfw.get_gamepad_state(jid)
                    if pad:
                        joystick=True;axes=pad.axes;dead=config.get('controller_deadzone',.12)
                        a=-float(axes[glfw.GAMEPAD_AXIS_LEFT_X]);control.steer=math.copysign(max(0,abs(a)-dead)/(1-dead),a)
                        control.throttle=(float(axes[glfw.GAMEPAD_AXIS_RIGHT_TRIGGER])+1)/2-(float(axes[glfw.GAMEPAD_AXIS_LEFT_TRIGGER])+1)/2
                        control.handbrake=bool(pad.buttons[glfw.GAMEPAD_BUTTON_A]);control.recover=bool(pad.buttons[glfw.GAMEPAD_BUTTON_Y])
                    break
            if args.benchmark:
                segment=int(max(0,elapsed-args.warmup)/22)%4
                desired=[0,math.pi/2,math.pi,-math.pi/2][segment]
                error=(desired-car.state.yaw+math.pi)%math.tau-math.pi
                control=Input(throttle=.72,steer=max(-1,min(1,error*2.5)))
            before=time.perf_counter()
            colliders=[(p.x,p.z,p.radius) for p in world.nearby_colliders(car.state.x,car.state.z,20)]
            traffic.update(0 if paused else dt,car.state.x,car.state.z,car.state.speed)
            colliders.extend(traffic.colliders(car.state.x,car.state.z))
            if control.recover:
                # Settle in a nearby unobstructed drivable position, not inside
                # the same cluster that prompted recovery.
                car.state.x,car.state.z=clear_position(world,car.state.x,car.state.z)
                colliders=[(p.x,p.z,p.radius) for p in world.nearby_colliders(car.state.x,car.state.z,20)]
                colliders.extend(traffic.colliders(car.state.x,car.state.z))
                camera=Camera();notice='RECOVERED';notice_until=elapsed+2
            alpha=car.advance(0 if paused else dt,control,world.surface_height,colliders)
            state=car.interpolate(alpha)
            physics_ms=(time.perf_counter()-before)*1000
            if state.collision_impulse>2:notice='CONTACT — brake, reverse, or R to settle';notice_until=elapsed+2
            if not paused:sim_time+=dt
            if audio:audio.set_speed(.3+abs(state.speed)/35)
            before=time.perf_counter()
            world.update(state.x,state.z)
            for key in world.drain_evicted():renderer.remove_mesh(key);chunk_instances.pop(key,None)
            for chunk in world.completed():accept(chunk)
            new_origin=(math.floor(state.x/world.config.chunk_size)*world.config.chunk_size,math.floor(state.z/world.config.chunk_size)*world.config.chunk_size)
            if new_origin!=origin:
                dx,dz=origin[0]-new_origin[0],origin[1]-new_origin[1]
                # Shift cached instance arrays instead of rebuilding tens of
                # thousands of Python prop records at every chunk crossing.
                for groups in chunk_instances.values():
                    for rows in groups.values():
                        rows[:,0]+=dx;rows[:,2]+=dz
                origin=new_origin
            stream_ms=(time.perf_counter()-before)*1000
            eye,target=camera.update(state,world,max(dt,1/240),mode,args.reduced_motion or config.get('reduced_motion',False))
            offset=np.array([origin[0],0,origin[1]]);eye_local=eye-offset;target_local=target-offset
            fog_distance=(world.config.radius-.4)*world.config.chunk_size
            batches=[];groups={}
            forward=normalize(target_local-eye_local)
            for key,chunk in world.chunks.items():
                center=np.array([chunk.origin[0]+world.config.chunk_size/2,0,chunk.origin[1]+world.config.chunk_size/2])-offset
                rel=center-eye_local
                if np.linalg.norm(rel[[0,2]])>fog_distance+110 or np.dot(rel,forward)<-120:continue
                batches.append((key,instance(chunk.origin[0]-origin[0],0,chunk.origin[1]-origin[1],material=1)))
                for kind,data in chunk_instances[key].items():
                    if kind=='walker0':
                        data=data.copy()
                        walk_frames=(np.floor(sim_time*9+data[:,2]*.17).astype(int)%8)
                        phase=sim_time*.42+data[:,2]*.013
                        travel=np.sin(phase)*6
                        data[:,0]+=np.sin(data[:,3])*travel
                        data[:,2]+=np.cos(data[:,3])*travel
                        data[:,1]+=.11
                        data[:,3]+=np.where(np.cos(phase)>0,math.pi,0)
                        kind=f'walker{int(sim_time*9)%8}'
                    rel=data[:,:3]-eye_local
                    limit=90 if kind=='grass' else 140 if kind.startswith('walker') else fog_distance*.92
                    mask=(np.sum(rel*rel,axis=1)<limit**2)&(rel@forward>-20)
                    if kind.startswith('walker'):
                        for pose in range(8):
                            selected=mask&(walk_frames==pose)
                            if np.any(selected):groups.setdefault(f'walker{pose}',[]).append(data[selected])
                        continue
                    if kind=='broadleaf':
                        far=np.sum(rel*rel,axis=1)>85**2
                        if np.any(mask&far):groups.setdefault('broadleaf_lod',[]).append(data[mask&far])
                        mask&=~far
                    if np.any(mask):groups.setdefault(kind,[]).append(data[mask])
            for kind,data in groups.items():batches.append((kind,np.concatenate(data)))
            # NPZ chassis includes wheel-relative ground offsets; spring height is above ground.
            base_y=state.y-car.config.ride_height
            batches.append(('car_body',instance(state.x-origin[0],base_y,state.z-origin[1],state.yaw,pitch=state.pitch,roll=state.roll,material=4)))
            wheels=[]
            for x in (-.86,.86):
                for z in (-1.35,1.35):
                    wx=state.x+math.cos(state.yaw)*x+math.sin(state.yaw)*z
                    wz=state.z-math.sin(state.yaw)*x+math.cos(state.yaw)*z
                    wy=world.surface_height(wx,wz)+.36
                    wheels.append(instance(wx-origin[0],wy,wz-origin[1],state.yaw+(state.steer if z<0 else 0),pitch=-state.wheel_spin,material=4)[0])
            batches.append(('car_wheel',np.asarray(wheels,dtype=np.float32)))
            traffic_bodies=[];traffic_wheels=[]
            for other in traffic.vehicles:
                if math.hypot(other.x-state.x,other.z-state.z)>fog_distance:continue
                row=instance(other.x-origin[0],other.y,other.z-origin[1],other.yaw,material=4)[0]
                row[8:11]=other.color
                traffic_bodies.append(row)
                for x in (-.86,.86):
                    for z in (-1.35,1.35):
                        wx=other.x+math.cos(other.yaw)*x+math.sin(other.yaw)*z
                        wz=other.z-math.sin(other.yaw)*x+math.cos(other.yaw)*z
                        traffic_wheels.append(instance(wx-origin[0],world.surface_height(wx,wz)+.36,wz-origin[1],other.yaw,pitch=-sim_time*other.speed/.36,material=4)[0])
            if traffic_bodies:
                # Each renderer receives a single batch per mesh key.
                for i,(key,rows) in enumerate(batches):
                    if key=='car_body':batches[i]=(key,np.concatenate((rows,np.array(traffic_bodies,dtype=np.float32))))
                    if key=='car_wheel':batches[i]=(key,np.concatenate((rows,np.array(traffic_wheels,dtype=np.float32))))
            hour=(args.time+(sim_time/30 if args.cycle else 0))%24
            angle=(hour-6)/24*math.tau
            sun_dir=normalize([-.65,math.sin(angle),-.45])
            daylight=max(.06,min(1,(math.sin(angle)+.12)*2.5))
            if sun_dir[1]<.06:sun_dir=normalize([-.5,.22,-.6])
            sun=[*sun_dir,daylight]
            fog=np.array([.64,.70,.73])*daylight+np.array([.025,.036,.075])*(1-daylight)
            rain={'clear':0.,'rain':.65,'storm':1.}[args.weather]
            if rain:
                fog=fog*(1-rain*.22);sun[3]*=1-rain*.4;fog_distance*=1-rain*.18
            if music:music.update(dt,state.speed,sun[3],rain,world.biome(state.x,state.z))
            street_lamp=[0.,-1000.,0.]
            lamp_distance=float('inf')
            for key,rows in batches:
                if key!='village':continue
                for row in rows:
                    a=row[3]
                    position=[row[0]+math.cos(a)*.65-math.sin(a)*7,row[1]+5.35,row[2]-math.sin(a)*.65-math.cos(a)*7]
                    distance=(position[0]-(state.x-origin[0]))**2+(position[2]-(state.z-origin[1]))**2
                    if distance<lamp_distance:street_lamp=position;lamp_distance=distance
            fps=fps*.95+.05/max(frame_interval,.001)
            capture=None
            stop=(args.frames and frame+1>=args.frames) or (args.benchmark and elapsed>=args.benchmark+args.warmup)
            if (stop and args.screenshot) or glfw.KEY_F12 in pressed:
                capture=str(args.screenshot or ROOT/'development/reference/capture.png')
            update_hud=elapsed>=hud_due or bool(capture)
            if update_hud and not args.no_hud:
                hud=render_hud(width,height,state,dict(quality=quality,backend=backend,seed=world.config.seed,biome=world.biome(state.x,state.z),fps=fps,paused=paused,help=show_help,time=hour,notice=notice if elapsed<notice_until else '',controller=joystick))
                hud_due=elapsed+.1
            stats=renderer.draw(batches,eye_local,target_local,sun,fog.tolist(),fog_distance,sim_time,hud_image=hud if update_hud else None,capture=capture,
                effects={'car':[state.x-origin[0],state.y,state.z-origin[1],state.yaw],'brake':max(control.brake,float(control.throttle<0 and state.speed>1)),'rain':rain,
                    'texture_origin':[origin[0]%32768,origin[1]%32768],'street_lamp':street_lamp})
            if elapsed>args.warmup:
                if measured_dropped is None:measured_dropped=car.dropped_time
                samples.append(frame_interval*1000);physics_samples.append(physics_ms);stream_samples.append(stream_ms);render_samples.append(stats['cpu_submit_ms'])
                if stats.get('gpu_ms') is not None:gpu_samples.append(stats['gpu_ms'])
            peak_chunks=max(peak_chunks,len(world.chunks));peak_pending=max(peak_pending,world.pending_count);peak_meshes=max(peak_meshes,len(renderer.meshes))
            if frame%120==0:
                route.append(dict(seconds=round(elapsed,2),x=round(state.x,2),z=round(state.z,2),speed=round(state.speed,2),chunks=len(world.chunks),rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1048576))
                renderer.set_title(f'ROADS / {quality} / {backend.upper()} / {fps:.0f} FPS')
            frame+=1
            if stop:break
            if not args.uncapped:
                remaining=1/60-(time.perf_counter()-now)
                # macOS sleep commonly overshoots ~1 ms. Sleep coarsely then
                # finish the short remainder against a monotonic deadline.
                if remaining>.0012:time.sleep(remaining-.0012)
                while time.perf_counter()-now<1/60:pass
        duration=time.perf_counter()-start
        def describe(values):
            a=np.array(values)
            return dict(mean_ms=float(a.mean()),p99_ms=float(np.percentile(a,99)),peak_ms=float(a.max())) if len(a) else None
        report=dict(backend=renderer.info,quality=quality,resolution=[width,height],world=asdict(world.config),vehicle=asdict(car.config),
            duration_seconds=duration,warmup_seconds=args.warmup,measured_frames=len(samples),vsync=not args.uncapped,frame=describe(samples),physics=describe(physics_samples),stream=describe(stream_samples),cpu_submit=describe(render_samples),gpu_scene=describe(gpu_samples),
            average_fps=1000/np.mean(samples) if samples else None,one_percent_low_fps=1000/np.percentile(samples,99) if samples else None,
            cpu_one_core_percent=(time.process_time()-cpu_start)/duration*100,max_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1048576,
            peak_chunks=peak_chunks,peak_pending=peak_pending,peak_meshes=peak_meshes,generation=describe(world.generation_times_ms),last_render=stats,route=list(route),
            limitations=['GPU timestamps bracket shadow, scene and rain; Metal excludes HUD/presentation, OpenGL includes HUD but excludes blit/presentation','RSS is process high-water mark; unified driver allocation not separately available'],
            physics_dropped_seconds=car.dropped_time-(measured_dropped or 0),startup_dropped_seconds=measured_dropped,
            weather=args.weather,time_hour=args.time,replay=str(args.replay) if args.replay else None,input_events=list(input_events),
            audio_enabled=audio is not None,music_enabled=music is not None,
            music_profile=asdict(music.current) if music else None,
            population={'traffic':len(traffic.vehicles),'traffic_budget':traffic.count,
                        'pedestrians':sum(p.kind=='walker0' for chunk in world.chunks.values() for p in chunk.props)},
            foliage_lod_metres=85,grass_cull_metres=90)
        if args.report:
            args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2),flush=True)
    finally:
        if music:music.stop()
        if audio:audio.stop()
        world.close()
        if renderer:renderer.close()

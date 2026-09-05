"""GPU-resident wgpu renderer. Explicit Metal on macOS, no frame readback except captures."""
import os
import sys
import time
from pathlib import Path
import numpy as np
import glfw
import wgpu
from rendercanvas.glfw import RenderCanvas
from .render_math import look_at, perspective, light_matrix


class MetalRenderer:
    def __init__(self, width, height, title, vsync=True, shadow_size=2048):
        if sys.platform == 'darwin':
            os.environ['WGPU_BACKEND_TYPE'] = 'Metal'
        self.width,self.height=width,height
        self.canvas=RenderCanvas(size=(width,height),title=title,vsync=vsync,update_mode='manual')
        self.window=self.canvas._window
        self.context=self.canvas.get_wgpu_context()
        adapter=wgpu.gpu.request_adapter_sync(power_preference='high-performance')
        self.info=dict(adapter.info)
        features=['timestamp-query'] if 'timestamp-query' in adapter.features else []
        self.device=adapter.request_device_sync(required_features=features)
        d=self.device
        self.format=self.context.get_preferred_format(adapter)
        self.context.configure(device=d,format=self.format)
        self.meshes={};self.instance_buffers={}; self.frame=0;self.gpu_ms=None
        self.shadow_size=shadow_size
        self.uniform=d.create_buffer(size=272,usage=wgpu.BufferUsage.UNIFORM|wgpu.BufferUsage.COPY_DST)
        self.shadow=d.create_texture(size=(shadow_size,shadow_size,1),format='depth32float',usage=wgpu.TextureUsage.RENDER_ATTACHMENT|wgpu.TextureUsage.TEXTURE_BINDING)
        self.shadow_view=self.shadow.create_view()
        self.layout=d.create_bind_group_layout(entries=[
            {'binding':0,'visibility':3,'buffer':{'type':'uniform'}},
            {'binding':1,'visibility':2,'texture':{'sample_type':'depth'}},
            {'binding':2,'visibility':2,'sampler':{'type':'comparison'}}])
        self.group=d.create_bind_group(layout=self.layout,entries=[{'binding':0,'resource':{'buffer':self.uniform}},
            {'binding':1,'resource':self.shadow_view},{'binding':2,'resource':d.create_sampler(compare='less-equal',mag_filter='linear',min_filter='linear')}])
        shader=d.create_shader_module(code=Path(__file__).with_name('shaders.wgsl').read_text())
        vertex_buffers=[{'array_stride':36,'step_mode':'vertex','attributes':[{'format':'float32x3','offset':i*12,'shader_location':i} for i in range(3)]},
                        {'array_stride':48,'step_mode':'instance','attributes':[{'format':'float32x4','offset':i*16,'shader_location':i+3} for i in range(3)]}]
        layout=d.create_pipeline_layout(bind_group_layouts=[self.layout])
        self.pipeline=d.create_render_pipeline(layout=layout,vertex={'module':shader,'entry_point':'vertex','buffers':vertex_buffers},
            primitive={'topology':'triangle-list','cull_mode':'none'},depth_stencil={'format':'depth32float','depth_write_enabled':True,'depth_compare':'less-equal'},
            fragment={'module':shader,'entry_point':'fragment','targets':[{'format':'rgba8unorm'}]})
        # Shadow pass binds only uniforms; sampling a depth attachment in the same pass is invalid.
        sl=d.create_bind_group_layout(entries=[{'binding':0,'visibility':1,'buffer':{'type':'uniform'}}])
        self.shadow_group=d.create_bind_group(layout=sl,entries=[{'binding':0,'resource':{'buffer':self.uniform}}])
        self.shadow_pipeline=d.create_render_pipeline(layout=d.create_pipeline_layout(bind_group_layouts=[sl]),
            vertex={'module':shader,'entry_point':'shadow_vertex','buffers':vertex_buffers},primitive={'topology':'triangle-list','cull_mode':'none'},
            depth_stencil={'format':'depth32float','depth_write_enabled':True,'depth_compare':'less','depth_bias':2,'depth_bias_slope_scale':2.})
        self.sky_pipeline=d.create_render_pipeline(layout=layout,vertex={'module':shader,'entry_point':'screen_vertex'},
            fragment={'module':shader,'entry_point':'sky_fragment','targets':[{'format':'rgba8unorm'}]},
            depth_stencil={'format':'depth32float','depth_write_enabled':False,'depth_compare':'always'})
        self.rain_pipeline=d.create_render_pipeline(layout=layout,vertex={'module':shader,'entry_point':'screen_vertex'},
            fragment={'module':shader,'entry_point':'rain_fragment','targets':[{'format':'rgba8unorm','blend':{'color':{'src_factor':'src-alpha','dst_factor':'one-minus-src-alpha','operation':'add'},'alpha':{'src_factor':'one','dst_factor':'one-minus-src-alpha','operation':'add'}}}]},
            depth_stencil={'format':'depth32float','depth_write_enabled':False,'depth_compare':'always'})
        usage=wgpu.TextureUsage.RENDER_ATTACHMENT|wgpu.TextureUsage.TEXTURE_BINDING|wgpu.TextureUsage.COPY_SRC
        self.color=d.create_texture(size=(width,height,1),format='rgba8unorm',usage=usage)
        self.depth=d.create_texture(size=(width,height,1),format='depth32float',usage=wgpu.TextureUsage.RENDER_ATTACHMENT)
        self.hud=d.create_texture(size=(width,height,1),format='rgba8unorm',usage=wgpu.TextureUsage.TEXTURE_BINDING|wgpu.TextureUsage.COPY_DST)
        self._init_present()
        if features:
            self.queries=d.create_query_set(type='timestamp',count=2)
            self.query_buffer=d.create_buffer(size=16,usage=wgpu.BufferUsage.QUERY_RESOLVE|wgpu.BufferUsage.COPY_SRC)
        else:self.queries=None

    def _init_present(self):
        d=self.device
        code='''@group(0) @binding(0) var tex:texture_2d<f32>;
        @group(0) @binding(1) var hud:texture_2d<f32>;
        @group(0) @binding(2) var s:sampler;
        struct O{@builtin(position) p:vec4<f32>,@location(0) uv:vec2<f32>};
        @vertex fn vs(@builtin(vertex_index)i:u32)->O{var a=array<vec2<f32>,3>(vec2(-1.,-1.),vec2(3.,-1.),vec2(-1.,3.));var o:O;o.p=vec4(a[i],0.,1.);o.uv=vec2(a[i].x*.5+.5,.5-a[i].y*.5);return o;}
        @fragment fn fs(o:O)->@location(0) vec4<f32>{let c=textureSample(tex,s,o.uv);let h=textureSample(hud,s,o.uv);let v=1.-.12*pow(length(o.uv-vec2(.5)),2.);return vec4(mix(c.rgb*v,h.rgb,h.a),1.);}'''
        # Surface sRGB conversion must not double encode our display-referred palette.
        if self.format.endswith('srgb'):
            code=code.replace('mix(c.rgb*v,h.rgb,h.a)','pow(mix(c.rgb*v,h.rgb,h.a),vec3(2.2))')
        sh=d.create_shader_module(code=code)
        self.present_pipeline=d.create_render_pipeline(layout='auto',vertex={'module':sh,'entry_point':'vs'},fragment={'module':sh,'entry_point':'fs','targets':[{'format':self.format}]})
        self.present_group=d.create_bind_group(layout=self.present_pipeline.get_bind_group_layout(0),entries=[
            {'binding':0,'resource':self.color.create_view()},{'binding':1,'resource':self.hud.create_view()},
            {'binding':2,'resource':d.create_sampler(mag_filter='linear',min_filter='linear')}])

    def upload_mesh(self,key,vertices):
        self.remove_mesh(key)
        self.meshes[key]=(self.device.create_buffer_with_data(data=np.ascontiguousarray(vertices,dtype=np.float32),usage=wgpu.BufferUsage.VERTEX),len(vertices))

    def remove_mesh(self,key):
        if key in self.meshes:self.meshes.pop(key)[0].destroy()
        if key in self.instance_buffers:self.instance_buffers.pop(key)[0].destroy()

    def draw(self,batches,camera,target,sun,fog_color,fog_distance,time,hud_image=None,capture=None,effects=None):
        started=__import__('time').perf_counter();d=self.device
        effects=effects or {}
        view,r,u,f=look_at(np.asarray(camera),target)
        vp=perspective(self.width/self.height)@view
        light=light_matrix(target,sun[:3])
        values=np.concatenate((vp.T.ravel(),light.T.ravel(),[*camera,1],sun,[*fog_color,1],
            [fog_distance,self.width/self.height,time,self.shadow_size],[*r,0],[*u,0],[*f,0],effects.get('car',[0,0,0,0]),
            [effects.get('rain',0),effects.get('brake',0),0,0])).astype(np.float32)
        d.queue.write_buffer(self.uniform,0,values)
        if hud_image is not None:
            d.queue.write_texture({'texture':self.hud},np.asarray(hud_image,dtype=np.uint8),{'bytes_per_row':self.width*4},(self.width,self.height,1))
        prepared=[];triangles=0
        for key,instances in batches:
            if not len(instances) or key not in self.meshes:continue
            instances=np.ascontiguousarray(instances,dtype=np.float32)
            size=instances.nbytes
            if key not in self.instance_buffers or self.instance_buffers[key][1]<size:
                if key in self.instance_buffers:self.instance_buffers[key][0].destroy()
                self.instance_buffers[key]=(d.create_buffer(size=max(size,4096),usage=wgpu.BufferUsage.VERTEX|wgpu.BufferUsage.COPY_DST),max(size,4096))
            ib=self.instance_buffers[key][0];d.queue.write_buffer(ib,0,instances)
            vb,n=self.meshes[key];prepared.append((vb,n,ib,len(instances),key))
            triangles+=n//3*len(instances)
        enc=d.create_command_encoder()
        timing={'query_set':self.queries,'beginning_of_pass_write_index':0} if self.queries else None
        kwargs={'timestamp_writes':timing} if timing else {}
        shadowpass=enc.begin_render_pass(color_attachments=[],depth_stencil_attachment={'view':self.shadow_view,'depth_clear_value':1.,'depth_load_op':'clear','depth_store_op':'store'},**kwargs)
        shadowpass.set_pipeline(self.shadow_pipeline);shadowpass.set_bind_group(0,self.shadow_group)
        for vb,n,ib,count,key in prepared:
            shadowpass.set_vertex_buffer(0,vb);shadowpass.set_vertex_buffer(1,ib);shadowpass.draw(n,count)
        shadowpass.end()
        p=enc.begin_render_pass(color_attachments=[{'view':self.color.create_view(),'resolve_target':None,'clear_value':(*fog_color,1),'load_op':'clear','store_op':'store'}],
            depth_stencil_attachment={'view':self.depth.create_view(),'depth_clear_value':1.,'depth_load_op':'clear','depth_store_op':'store'})
        p.set_bind_group(0,self.group);p.set_pipeline(self.sky_pipeline);p.draw(3)
        p.set_pipeline(self.pipeline)
        for vb,n,ib,count,key in prepared:
            p.set_vertex_buffer(0,vb);p.set_vertex_buffer(1,ib);p.draw(n,count)
        if effects.get('rain',0)>0:
            p.set_pipeline(self.rain_pipeline);p.draw(3)
        p.end()
        if self.queries:
            # Apple/wgpu-native currently returns zero for end-of-pass timestamps.
            # Beginning of a subsequent empty pass provides a valid ordered marker.
            marker=enc.begin_render_pass(color_attachments=[{'view':self.color.create_view(),'resolve_target':None,'load_op':'load','store_op':'store'}],
                timestamp_writes={'query_set':self.queries,'beginning_of_pass_write_index':1})
            marker.end()
            enc.resolve_query_set(self.queries,0,2,self.query_buffer,0)
        d.queue.submit([enc.finish()])
        def present():
            e=d.create_command_encoder()
            p=e.begin_render_pass(color_attachments=[{'view':self.context.get_current_texture().create_view(),'resolve_target':None,'clear_value':(0,0,0,1),'load_op':'clear','store_op':'store'}])
            p.set_pipeline(self.present_pipeline);p.set_bind_group(0,self.present_group);p.draw(3);p.end();d.queue.submit([e.finish()])
        self.canvas.request_draw(present);self.canvas.force_draw()
        submit_ms=(__import__('time').perf_counter()-started)*1000
        self.frame+=1
        if self.queries and self.frame%120==0:
            ts=np.frombuffer(d.queue.read_buffer(self.query_buffer),dtype=np.uint64)
            delta=int(ts[1])-int(ts[0])
            # Drivers may return unavailable/disjoint timestamp pairs. Never wrap
            # unsigned subtraction into a plausible-looking performance result.
            self.gpu_ms=delta/1e6 if 0<delta<1_000_000_000 else None
        if capture:
            from PIL import Image
            data=d.queue.read_texture({'texture':self.color},{'bytes_per_row':self.width*4},(self.width,self.height,1))
            im=Image.fromarray(np.frombuffer(data,dtype=np.uint8).reshape(self.height,self.width,4),'RGBA')
            if hud_image is not None:im=Image.alpha_composite(im,hud_image)
            Path(capture).parent.mkdir(parents=True,exist_ok=True);im.save(capture)
        return {'cpu_submit_ms':submit_ms,'gpu_ms':self.gpu_ms,'draw_calls':len(prepared)*2+2+int(effects.get('rain',0)>0),'triangles':triangles}

    def should_close(self):return self.canvas.get_closed()
    def poll(self):glfw.poll_events()
    def set_title(self,title):glfw.set_window_title(self.window,title)
    def close(self):
        for key in list(self.meshes):self.remove_mesh(key)
        self.canvas.close()
        self.device.destroy()

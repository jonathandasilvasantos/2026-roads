"""Instanced OpenGL fallback for Macs without a compatible WebGPU runtime."""
import ctypes
import math
import time as clock
from pathlib import Path

import glfw
import numpy as np
from OpenGL import GL as gl
from OpenGL.GL.shaders import compileProgram, compileShader


def normalize(v):
    a = np.asarray(v, dtype=np.float32)
    return a / max(float(np.linalg.norm(a)), 1e-8)


def look_at(eye, target):
    eye = np.asarray(eye, dtype=np.float32)
    forward = normalize(np.asarray(target) - eye)
    side = normalize(np.cross(forward, (0, 1, 0)))
    if np.linalg.norm(side) < .1:
        side = np.array((1, 0, 0), dtype=np.float32)
    up = np.cross(side, forward)
    m = np.eye(4, dtype=np.float32)
    m[:3, :3] = np.array((side, up, -forward))
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def perspective(aspect):
    f, near, far = 1 / math.tan(math.pi / 6), .12, 2400.
    return np.array(((f/aspect, 0, 0, 0), (0, f, 0, 0),
                     (0, 0, (far+near)/(near-far), 2*far*near/(near-far)),
                     (0, 0, -1, 0)), dtype=np.float32)


VERT = '''#version 330 core
layout(location=0) in vec3 position;
layout(location=1) in vec3 normal;
layout(location=2) in vec3 color;
layout(location=3) in vec4 placement;
layout(location=4) in vec4 scaling;
layout(location=5) in vec4 tint;
uniform mat4 vp; uniform mat4 lightVP;
out vec3 world; out vec3 norm; out vec3 col;
out vec4 shadowPos; out float material;
void main(){
 float c=cos(placement.w),s=sin(placement.w);
 mat3 rotation=mat3(c,0,-s, 0,1,0, s,0,c);
 if(tint.w>3.) {
  float p=cos(scaling.w),q=sin(scaling.w);
  float r=cos(tint.w-4.),t=sin(tint.w-4.);
  rotation=rotation*mat3(r,t,0, -t,r,0, 0,0,1)*mat3(1,0,0, 0,p,q, 0,-q,p);
 }
 world=rotation*(position*scaling.xyz)+placement.xyz;
 norm=normalize(rotation*(normal/max(abs(scaling.xyz),vec3(.0001))));
 col=color*tint.rgb; material=tint.w;
 shadowPos=lightVP*vec4(world,1);
 gl_Position=vp*vec4(world,1);
}'''

SKY_FUNCTIONS = '''
float hash(vec2 p){p=mod(p,4096.);return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float noise(vec2 p){
 vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);
 return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y);
}
vec3 sky_color(vec3 ray){
 vec3 c=mix(fogColor,mix(vec3(.025,.042,.085),vec3(.23,.43,.60),daylight),pow(max(ray.y,0.),.48));
 float alignment=max(dot(ray,sun),0.);
 c+=vec3(1.,.64,.29)*(pow(alignment,120.)*.18+smoothstep(.9995,.99985,alignment)*1.8)*daylight;
 if(ray.y>.02){
  vec2 uv=ray.xz/(ray.y+.16)*2.2+vec2(elapsedTime*.002,0.);
  float clouds=smoothstep(.55,.73,noise(uv)*.48+noise(uv*2.7)*.27+noise(uv*7.3)*.16+noise(uv*18.)*.09)*smoothstep(.03,.25,ray.y);
  c=mix(c,mix(vec3(.11,.14,.21),vec3(.89,.87,.79),daylight),clouds*.5);
 }
 return c;
}
'''

FRAG = '''#version 330 core
in vec3 world; in vec3 norm; in vec3 col; in vec4 shadowPos; in float material;
uniform vec3 camera; uniform vec3 sun; uniform vec3 fogColor;
uniform mat4 lightVP; uniform float elapsedTime;
uniform vec4 car; uniform vec4 effects;
uniform float fogDistance; uniform float daylight; uniform sampler2D shadowTex; uniform int useShadow;
out vec4 outputColor;
''' + SKY_FUNCTIONS + '''
void main(){
 vec3 n=normalize(norm); float ndl=max(dot(n,sun),0);
 vec4 lp=lightVP*vec4(world+n*.065,1.);
 float shade=1.; vec3 sp=lp.xyz/lp.w*.5+.5;
 if(useShadow==1 && all(greaterThan(sp,vec3(.002))) && all(lessThan(sp,vec3(.998)))) {
   float bias=.0015; shade=0.;
   vec2 texel=1./vec2(textureSize(shadowTex,0));
   for(int x=-1;x<=1;x++) for(int y=-1;y<=1;y++)
    shade+=sp.z-bias<=texture(shadowTex,sp.xy+vec2(x,y)*texel).r?1.:0.;
   shade/=9.;
 }
 vec3 base=col;
 if(material<1.5){vec2 tw=world.xz+effects.zw;base*=.92+.16*noise(tw*6.);base*=.94+.12*noise(tw*.125);}
 vec3 ambient=mix(vec3(.075,.10,.16),vec3(.38,.43,.48),daylight);
 float hemi=.70+.30*max(n.y,0.);
 vec3 lit=base*(ambient*hemi+vec3(1.,.86,.67)*ndl*shade*.80*daylight);
 vec3 view=normalize(camera-world);
 float spec=pow(max(dot(n,normalize(sun+view)),0.),48.);
 if(material>3.) {
  lit+=vec3(1.,.86,.65)*spec*.32*shade*daylight;
  if(col.b>col.r*1.4 && col.g>.05)
   lit=mix(lit,sky_color(reflect(-view,n))*.65,.18+.50*pow(1.-max(dot(n,view),0.),4.));
  if(col.r>.3 && col.g<.06)lit+=vec3(1.,.015,.005)*(.25+.8*effects.y);
  if(col.r>.85 && col.g>.7)lit+=vec3(1.,.94,.75)*.35*(1.-daylight);
 }
 vec3 lightDelta=world-(car.xyz+vec3(0,1,0));
 float lightDistance=length(lightDelta);
 vec3 direction=normalize(vec3(-sin(car.w),-.08,-cos(car.w)));
 float cone=smoothstep(.93,.98,dot(normalize(lightDelta),direction));
 float attenuation=1./(1.+lightDistance*lightDistance*.009)* (1.-smoothstep(45.,60.,lightDistance));
 float headlight=cone*attenuation*max(n.y,0.)*(1.-daylight)*2.2;
 lit+=col*vec3(1.,.92,.72)*headlight;
 if(material<1.5 && abs(col.r-col.g)<.06 && col.r<.35){
  lit*=1.-effects.x*.15;
  lit+=vec3(.7,.8,.9)*spec*effects.x*.35*daylight+vec3(1.,.91,.7)*headlight*effects.x*.25;
 }
 float distanceToCamera=length(world-camera);
 float fog=1.-exp(-pow(distanceToCamera/max(fogDistance,1.),2.5)*2.4);
 lit=mix(lit,sky_color(normalize(world-camera)),clamp(fog,0.,1.));
 lit=lit/(lit+vec3(.7))*1.32;
 outputColor=vec4(pow(max(lit,vec3(0)),vec3(.85)),1);
}'''

QUAD = '''#version 330 core
out vec2 uv;
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);uv=p;
 gl_Position=vec4(p*2.-1.,0,1);}'''

SKY = '''#version 330 core
in vec2 uv; out vec4 outputColor;
uniform mat4 invVP; uniform vec3 camera; uniform vec3 sun; uniform vec3 fogColor; uniform float daylight; uniform float elapsedTime;
''' + SKY_FUNCTIONS + '''
void main(){
 vec4 p=invVP*vec4(uv*2.-1.,1,1); vec3 ray=normalize(p.xyz/p.w-camera);
 outputColor=vec4(sky_color(ray),1);
}'''

HUD = '''#version 330 core
in vec2 uv; out vec4 outputColor; uniform sampler2D image;
void main(){outputColor=texture(image,vec2(uv.x,1.-uv.y));}'''

RAIN = '''#version 330 core
in vec2 uv;out vec4 outputColor;
uniform float elapsedTime;uniform float rain;
float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
void main(){
 vec2 p=vec2(uv.x+uv.y*.13,uv.y)*vec2(130.,60.);
 p.y+=elapsedTime*35.;
 vec2 cell=floor(p);vec2 f=fract(p);
 float streak=(1.-smoothstep(.015,.045,abs(f.x-.5)))*smoothstep(.03,.16,f.y)*(1.-smoothstep(.65,.98,f.y));
 streak*=step(.67,hash(cell));
 outputColor=vec4(.72,.81,.89,streak*rain*.12);
}'''


class GLRenderer:
    def __init__(self, width, height, title, vsync=True, shadow_size=2048):
        self.width, self.height = int(width), int(height)
        self.shadow_size = int(shadow_size)
        if not glfw.init():
            raise RuntimeError('GLFW initialization failed')
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 4)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 1)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, True)
        glfw.window_hint(glfw.COCOA_RETINA_FRAMEBUFFER, False)
        self.window = glfw.create_window(self.width, self.height, title, None, None)
        if not self.window:
            glfw.terminate()
            raise RuntimeError('OpenGL 4.1 core context creation failed')
        glfw.make_context_current(self.window)
        glfw.swap_interval(1 if vsync else 0)
        self.info = {name: gl.glGetString(token).decode() for name, token in (
            ('vendor', gl.GL_VENDOR), ('renderer', gl.GL_RENDERER), ('version', gl.GL_VERSION))}
        self.info.update(backend='OpenGL', metal=False, resolution=[self.width,self.height])
        self.info['driver_uses_metal'] = 'Metal' in self.info['version']
        self.quad = gl.glGenVertexArrays(1)
        gl.glBindVertexArray(self.quad)
        self.program = self._program(VERT, FRAG)
        self.shadow_program = self._program(VERT, '#version 330 core\nvoid main(){}')
        self.sky_program = self._program(QUAD, SKY)
        self.hud_program = self._program(QUAD, HUD)
        self.rain_program = self._program(QUAD, RAIN)
        self.meshes = {}
        self.fbo = gl.glGenFramebuffers(1)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.fbo)
        self.color = self._texture(self.width, self.height)
        gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, self.color, 0)
        self.depth = gl.glGenRenderbuffers(1)
        gl.glBindRenderbuffer(gl.GL_RENDERBUFFER, self.depth)
        gl.glRenderbufferStorage(gl.GL_RENDERBUFFER, gl.GL_DEPTH_COMPONENT24, self.width, self.height)
        gl.glFramebufferRenderbuffer(gl.GL_FRAMEBUFFER, gl.GL_DEPTH_ATTACHMENT, gl.GL_RENDERBUFFER, self.depth)
        self._check_fbo()
        self.shadow_fbo = gl.glGenFramebuffers(1)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.shadow_fbo)
        self.shadow = self._texture(max(1,self.shadow_size), max(1,self.shadow_size), depth=True)
        gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_DEPTH_ATTACHMENT, gl.GL_TEXTURE_2D, self.shadow, 0)
        gl.glDrawBuffer(gl.GL_NONE)
        gl.glReadBuffer(gl.GL_NONE)
        self._check_fbo()
        self.hud = self._texture(self.width, self.height)
        self.has_hud = False
        self.last_hud = -1.
        self.queries = list(gl.glGenQueries(6))
        self.free_queries = list(self.queries)
        self.pending_queries = []
        self.gpu_ms = None
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)

    def _program(self, vertex, fragment):
        return compileProgram(compileShader(vertex, gl.GL_VERTEX_SHADER), compileShader(fragment, gl.GL_FRAGMENT_SHADER))

    def _check_fbo(self):
        status = gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER)
        if status != gl.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f'Incomplete framebuffer: {status}')

    def _texture(self, width, height, depth=False):
        texture = gl.glGenTextures(1)
        gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
        gl.glTexImage2D(gl.GL_TEXTURE_2D,0,gl.GL_DEPTH_COMPONENT24 if depth else gl.GL_RGBA8,
                        width,height,0,gl.GL_DEPTH_COMPONENT if depth else gl.GL_RGBA,
                        gl.GL_FLOAT if depth else gl.GL_UNSIGNED_BYTE,None)
        for param in (gl.GL_TEXTURE_MIN_FILTER, gl.GL_TEXTURE_MAG_FILTER):
            gl.glTexParameteri(gl.GL_TEXTURE_2D,param,gl.GL_NEAREST if depth else gl.GL_LINEAR)
        for param in (gl.GL_TEXTURE_WRAP_S,gl.GL_TEXTURE_WRAP_T):
            gl.glTexParameteri(gl.GL_TEXTURE_2D,param,gl.GL_CLAMP_TO_EDGE)
        return texture

    def upload_mesh(self, key, vertices):
        self.remove_mesh(key)
        vertices = np.ascontiguousarray(vertices, dtype=np.float32).reshape(-1,9)
        vao = gl.glGenVertexArrays(1)
        vbo, instance = gl.glGenBuffers(2)
        gl.glBindVertexArray(vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER,vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER,vertices.nbytes,vertices,gl.GL_STATIC_DRAW)
        for location in range(3):
            gl.glEnableVertexAttribArray(location)
            gl.glVertexAttribPointer(location,3,gl.GL_FLOAT,False,36,ctypes.c_void_p(location*12))
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER,instance)
        for location in range(3,6):
            gl.glEnableVertexAttribArray(location)
            gl.glVertexAttribPointer(location,4,gl.GL_FLOAT,False,48,ctypes.c_void_p((location-3)*16))
            gl.glVertexAttribDivisor(location,1)
        self.meshes[key] = (vao,vbo,instance,len(vertices))

    def remove_mesh(self,key):
        if key in self.meshes:
            vao,vbo,instance,_ = self.meshes.pop(key)
            gl.glDeleteVertexArrays(1,[vao])
            gl.glDeleteBuffers(2,[vbo,instance])

    def _uniform(self, program, name, value):
        location = gl.glGetUniformLocation(program,name)
        if location < 0:
            return
        if name in ('shadowTex','useShadow','image'):
            gl.glUniform1i(location,int(value))
        elif np.isscalar(value):
            gl.glUniform1f(location,float(value))
        else:
            a=np.asarray(value,dtype=np.float32)
            if a.shape==(4,4):
                gl.glUniformMatrix4fv(location,1,True,a)
            elif a.shape==(4,):
                gl.glUniform4fv(location,1,a)
            else:
                gl.glUniform3fv(location,1,a)

    def draw(self,batches,camera,target,sun,fog_color,fog_distance,time,hud_image=None,capture=None,effects=None):
        start=clock.perf_counter()
        glfw.make_context_current(self.window)
        while self.pending_queries and gl.glGetQueryObjectuiv(self.pending_queries[0],gl.GL_QUERY_RESULT_AVAILABLE):
            query=self.pending_queries.pop(0)
            self.gpu_ms=float(gl.glGetQueryObjectuiv(query,gl.GL_QUERY_RESULT))/1e6
            self.free_queries.append(query)
        query=self.free_queries.pop() if self.free_queries else None
        if query is not None:
            gl.glBeginQuery(gl.GL_TIME_ELAPSED,query)
        vp=perspective(self.width/self.height) @ look_at(camera,target)
        daylight=float(sun[3]) if len(sun)>3 else 1.
        sun=normalize(sun[:3])
        effects=effects or {}
        rain=float(effects.get('rain',0))
        effect_vector=(rain,float(effects.get('brake',0)),*effects.get('texture_origin',(0.,0.)))
        car=effects.get('car',(*target,0.))
        focus=np.asarray(target,dtype=np.float32)
        eye=focus+sun*230
        light_projection=np.eye(4,dtype=np.float32)
        light_projection[0,0]=light_projection[1,1]=1/145
        light_projection[2,2]=-2/550
        light_projection[2,3]=-1
        lightvp=light_projection @ look_at(eye,focus)
        ready=[]
        grouped={}
        for key,instances in batches:
            if key not in self.meshes or len(instances)==0:
                continue
            grouped.setdefault(key,[]).append(np.asarray(instances,dtype=np.float32).reshape(-1,12))
        for key,groups in grouped.items():
            data=np.ascontiguousarray(groups[0] if len(groups)==1 else np.concatenate(groups),dtype=np.float32)
            mesh=self.meshes[key]
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER,mesh[2])
            gl.glBufferData(gl.GL_ARRAY_BUFFER,data.nbytes,data,gl.GL_STREAM_DRAW)
            ready.append((mesh,len(data)))
        gl.glEnable(gl.GL_DEPTH_TEST)
        gl.glDisable(gl.GL_BLEND)
        gl.glDisable(gl.GL_CULL_FACE)
        shadow_calls=0
        if self.shadow_size>0:
            gl.glBindFramebuffer(gl.GL_FRAMEBUFFER,self.shadow_fbo)
            gl.glViewport(0,0,self.shadow_size,self.shadow_size)
            gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
            gl.glUseProgram(self.shadow_program)
            self._uniform(self.shadow_program,'vp',lightvp)
            self._uniform(self.shadow_program,'lightVP',lightvp)
            gl.glEnable(gl.GL_POLYGON_OFFSET_FILL)
            gl.glPolygonOffset(1.5,3.)
            for mesh,count in ready:
                gl.glBindVertexArray(mesh[0])
                gl.glDrawArraysInstanced(gl.GL_TRIANGLES,0,mesh[3],count)
                shadow_calls+=1
            gl.glDisable(gl.GL_POLYGON_OFFSET_FILL)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER,self.fbo)
        gl.glViewport(0,0,self.width,self.height)
        gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
        gl.glDisable(gl.GL_DEPTH_TEST)
        gl.glUseProgram(self.sky_program)
        for name,value in [('invVP',np.linalg.inv(vp)),('camera',camera),('sun',sun),('fogColor',fog_color),('daylight',daylight),('elapsedTime',time)]:
            self._uniform(self.sky_program,name,value)
        gl.glBindVertexArray(self.quad)
        gl.glDrawArrays(gl.GL_TRIANGLES,0,3)
        gl.glEnable(gl.GL_DEPTH_TEST)
        gl.glUseProgram(self.program)
        for name,value in [('vp',vp),('lightVP',lightvp),('camera',camera),('sun',sun),
                           ('fogColor',fog_color),('fogDistance',fog_distance),('daylight',daylight),('elapsedTime',time),('car',car),('effects',effect_vector),('shadowTex',0),('useShadow',int(self.shadow_size>0))]:
            self._uniform(self.program,name,value)
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D,self.shadow)
        triangles=0
        for mesh,count in ready:
            gl.glBindVertexArray(mesh[0])
            gl.glDrawArraysInstanced(gl.GL_TRIANGLES,0,mesh[3],count)
            triangles+=mesh[3]//3*count
        if rain>0:
            gl.glDisable(gl.GL_DEPTH_TEST)
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA,gl.GL_ONE_MINUS_SRC_ALPHA)
            gl.glUseProgram(self.rain_program)
            self._uniform(self.rain_program,'elapsedTime',time)
            self._uniform(self.rain_program,'rain',rain)
            gl.glBindVertexArray(self.quad)
            gl.glDrawArrays(gl.GL_TRIANGLES,0,3)
            gl.glDisable(gl.GL_BLEND)
        if hud_image is not None and clock.perf_counter()-self.last_hud>=.095:
            image=np.ascontiguousarray(hud_image.convert('RGBA'),dtype=np.uint8)
            if image.shape!=(self.height,self.width,4):
                raise ValueError('HUD image must match internal render dimensions')
            gl.glBindTexture(gl.GL_TEXTURE_2D,self.hud)
            gl.glTexSubImage2D(gl.GL_TEXTURE_2D,0,0,0,self.width,self.height,gl.GL_RGBA,gl.GL_UNSIGNED_BYTE,image)
            self.has_hud=True
            self.last_hud=clock.perf_counter()
        if self.has_hud:
            gl.glDisable(gl.GL_DEPTH_TEST)
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA,gl.GL_ONE_MINUS_SRC_ALPHA)
            gl.glUseProgram(self.hud_program)
            gl.glBindTexture(gl.GL_TEXTURE_2D,self.hud)
            self._uniform(self.hud_program,'image',0)
            gl.glBindVertexArray(self.quad)
            gl.glDrawArrays(gl.GL_TRIANGLES,0,3)
            gl.glDisable(gl.GL_BLEND)
        if query is not None:
            gl.glEndQuery(gl.GL_TIME_ELAPSED)
            self.pending_queries.append(query)
        submit_ms=(clock.perf_counter()-start)*1000
        if capture:
            from PIL import Image
            gl.glPixelStorei(gl.GL_PACK_ALIGNMENT,1)
            pixels=gl.glReadPixels(0,0,self.width,self.height,gl.GL_RGBA,gl.GL_UNSIGNED_BYTE)
            path=Path(capture)
            path.parent.mkdir(parents=True,exist_ok=True)
            Image.frombytes('RGBA',(self.width,self.height),pixels).transpose(Image.Transpose.FLIP_TOP_BOTTOM).save(path)
        gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER,self.fbo)
        gl.glBindFramebuffer(gl.GL_DRAW_FRAMEBUFFER,0)
        w,h=glfw.get_framebuffer_size(self.window)
        gl.glBlitFramebuffer(0,0,self.width,self.height,0,0,w,h,gl.GL_COLOR_BUFFER_BIT,gl.GL_LINEAR)
        glfw.swap_buffers(self.window)
        return {'cpu_submit_ms':submit_ms,'gpu_ms':self.gpu_ms,'draw_calls':len(ready)+shadow_calls+1+int(self.has_hud)+int(rain>0),
                'triangles':triangles,'shadow_triangles':triangles if self.shadow_size else 0,
                'render_ms':(clock.perf_counter()-start)*1000}

    def should_close(self):
        return glfw.window_should_close(self.window)

    def poll(self):
        glfw.poll_events()

    def set_title(self,title):
        glfw.set_window_title(self.window,title)

    def close(self):
        glfw.make_context_current(self.window)
        for key in list(self.meshes):
            self.remove_mesh(key)
        gl.glDeleteTextures([self.color,self.shadow,self.hud])
        gl.glDeleteFramebuffers(2,[self.fbo,self.shadow_fbo])
        gl.glDeleteRenderbuffers(1,[self.depth])
        gl.glDeleteVertexArrays(1,[self.quad])
        gl.glDeleteQueries(len(self.queries),self.queries)
        for program in (self.program,self.shadow_program,self.sky_program,self.hud_program,self.rain_program):
            gl.glDeleteProgram(program)
        glfw.destroy_window(self.window)
        glfw.terminate()

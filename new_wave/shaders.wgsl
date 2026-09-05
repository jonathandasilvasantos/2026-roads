struct Globals {
    vp: mat4x4<f32>, light_vp: mat4x4<f32>, camera: vec4<f32>,
    sun: vec4<f32>, fog: vec4<f32>, params: vec4<f32>,
    right: vec4<f32>, up: vec4<f32>, forward: vec4<f32>,
    car: vec4<f32>, effects: vec4<f32>,
};
@group(0) @binding(0) var<uniform> g: Globals;
@group(0) @binding(1) var shadow: texture_depth_2d;
@group(0) @binding(2) var shadow_sampler: sampler_comparison;
@group(0) @binding(3) var ground: texture_2d<f32>;
@group(0) @binding(4) var ground_sampler: sampler;
struct Vert {
    @location(0) p: vec3<f32>, @location(1) n: vec3<f32>, @location(2) c: vec3<f32>,
    @location(3) pose: vec4<f32>, @location(4) scale: vec4<f32>, @location(5) tint: vec4<f32>,
};
struct Out {
    @builtin(position) p: vec4<f32>, @location(0) world: vec3<f32>,
    @location(1) n: vec3<f32>, @location(2) color: vec3<f32>, @location(3) material: f32,
};
fn rotate(p: vec3<f32>, a: f32) -> vec3<f32> {
    return vec3(cos(a)*p.x+sin(a)*p.z,p.y,-sin(a)*p.x+cos(a)*p.z);
}
@vertex fn vertex(v: Vert) -> Out {
    var o: Out;
    // scale.w and tint.w encode pitch/roll only for articulated car instances.
    var p=v.p*v.scale.xyz; var n=v.n/v.scale.xyz;
    if (v.tint.w > 3.) {
        let pitch=v.scale.w;
        p=vec3(p.x,cos(pitch)*p.y-sin(pitch)*p.z,sin(pitch)*p.y+cos(pitch)*p.z);
        n=vec3(n.x,cos(pitch)*n.y-sin(pitch)*n.z,sin(pitch)*n.y+cos(pitch)*n.z);
        let roll=v.tint.w-4.;
        p=vec3(cos(roll)*p.x-sin(roll)*p.y,sin(roll)*p.x+cos(roll)*p.y,p.z);
        n=vec3(cos(roll)*n.x-sin(roll)*n.y,sin(roll)*n.x+cos(roll)*n.y,n.z);
    }
    o.world=rotate(p,v.pose.w)+v.pose.xyz;
    o.n=normalize(rotate(n,v.pose.w)); o.color=v.c*v.tint.rgb;
    o.p=g.vp*vec4(o.world,1); o.material=v.tint.w;
    return o;
}
@vertex fn shadow_vertex(v: Vert) -> @builtin(position) vec4<f32> {
    var p=v.p*v.scale.xyz;
    if(v.tint.w>3.) {
        let a=v.scale.w;let r=v.tint.w-4.;
        p=vec3(p.x,cos(a)*p.y-sin(a)*p.z,sin(a)*p.y+cos(a)*p.z);
        p=vec3(cos(r)*p.x-sin(r)*p.y,sin(r)*p.x+cos(r)*p.y,p.z);
    }
    return g.light_vp*vec4(rotate(p,v.pose.w)+v.pose.xyz,1);
}
fn hash(p: vec2<f32>) -> f32 {
    let wrapped=p-floor(p/4096.)*4096.;
    return fract(sin(dot(wrapped,vec2(127.1,311.7)))*43758.5453);
}
fn noise(p:vec2<f32>)->f32 {
    let i=floor(p); var f=fract(p); f=f*f*(3.-2.*f);
    return mix(mix(hash(i),hash(i+vec2(1.,0.)),f.x),mix(hash(i+vec2(0.,1.)),hash(i+vec2(1.,1.)),f.x),f.y);
}
fn sky_color(ray:vec3<f32>)->vec3<f32> {
    let day=g.sun.w;
    var c=mix(g.fog.rgb,mix(vec3(.025,.042,.085),vec3(.23,.43,.60),day),pow(max(ray.y,0.),.48));
    let alignment=max(dot(ray,g.sun.xyz),0.);
    c+=vec3(1.,.64,.29)*(pow(alignment,120.)*.18+smoothstep(.9995,.99985,alignment)*1.8)*day;
    if(ray.y>.02) {
        let uv=ray.xz/(ray.y+.16)*2.2+vec2(g.params.z*.002,0.);
        let cloud_noise=noise(uv)*.48+noise(uv*2.7)*.27+noise(uv*7.3)*.16+noise(uv*18.)*.09;
        let clouds=smoothstep(.55,.73,cloud_noise)*smoothstep(.03,.25,ray.y);
        c=mix(c,mix(vec3(.11,.14,.21),vec3(.89,.87,.79),day),clouds*.5);
    }
    return c;
}
@fragment fn fragment(o:Out)->@location(0) vec4<f32> {
    let n=normalize(o.n); let d=distance(g.camera.xyz,o.world);
    var col=o.color;
    let ground_detail=textureSample(ground,ground_sampler,(o.world.xz+g.effects.zw)*.5).rgb;
    if(o.material<1.5) {
        let texture_world=o.world.xz+g.effects.zw+vec2(o.world.y*.73,o.world.y*.91)*(1.-abs(n.y));
        let grain=noise(texture_world*6.);
        col*=.92+.16*grain;
        col*=.94+.12*noise(texture_world*.125);
        if(o.material>.5 && o.color.r>.18 && o.color.g>o.color.r*1.08) {
            col*=mix(vec3(.7),ground_detail*2.5,.8);
        }
        if(o.material>.5 && o.color.r<.2 && o.color.g<.2) {
            let aggregate=noise(texture_world*28.);
            col*=.75+.50*aggregate;
            col+=vec3(.035,.030,.020)*smoothstep(.52,.75,noise(texture_world*.33));
        }
        if(o.material<.5 && o.color.r>.5 && abs(n.y)<.5) {
            col*=.80+.28*noise(texture_world*3.1);
        }
    }
    let lp=g.light_vp*vec4(o.world+n*.065,1.);
    let uv=lp.xy*.5+vec2(.5); let suv=vec2(uv.x,1.-uv.y);
    var shade=1.;
    if(all(suv>vec2(.002)) && all(suv<vec2(.998)) && lp.z>0. && lp.z<1.) {
        shade=0.;
        for(var x=-1;x<=1;x++) {for(var y=-1;y<=1;y++) {
            shade+=textureSampleCompare(shadow,shadow_sampler,suv+vec2(f32(x),f32(y))/g.params.w,lp.z-.0015)/9.;
        }}
    }
    let diffuse=max(dot(n,g.sun.xyz),0.);
    let ambient=mix(vec3(.075,.10,.16),vec3(.38,.43,.48),g.sun.w);
    let hemi=.70+.30*max(n.y,0.);
    var lit=col*(ambient*hemi+vec3(1.,.86,.67)*diffuse*shade*.80*g.sun.w);
    let view=normalize(g.camera.xyz-o.world);
    let spec=pow(max(dot(n,normalize(g.sun.xyz+view)),0.),48.);
    if(o.material>3.) {
        lit+=vec3(1.,.86,.65)*spec*.32*shade*g.sun.w;
        if(o.color.r>.25 && o.color.g>.07 && o.color.g<.5) {
            let coat=.035+.10*pow(1.-max(dot(n,view),0.),4.);
            lit=mix(lit,sky_color(reflect(-view,n)),coat);
        }
        if(o.color.b>o.color.r*1.4 && o.color.g>.05) {
            let reflected=sky_color(reflect(-view,n));
            let fresnel=.18+.50*pow(1.-max(dot(n,view),0.),4.);
            lit=mix(lit,reflected*.65,fresnel);
        }
        if(o.color.r>.3 && o.color.g<.06) {lit+=vec3(.8,.018,.005)*(.25+.8*g.effects.y);}
        if(o.color.r>.85 && o.color.g>.7) {lit+=vec3(.9,.78,.45)*.35*(1.-g.sun.w);}
    }
    let lamp_position=g.car.xyz+vec3(0.,.7,0.);
    let lamp_ray=o.world-lamp_position;
    let lamp_direction=normalize(vec3(-sin(g.car.w),-.08,-cos(g.car.w)));
    let beam=smoothstep(.93,.985,dot(normalize(lamp_ray),lamp_direction));
    let reach=1.-smoothstep(8.,60.,length(lamp_ray));
    lit+=col*vec3(1.,.89,.63)*beam*reach*max(n.y,.0)*2.2*(1.-g.sun.w);
    let street=vec3(g.right.w,g.up.w,g.forward.w)-o.world;
    lit+=col*vec3(1.,.75,.40)*max(dot(n,normalize(street)),0.)*3./(1.+dot(street,street)*.09)*(1.-g.sun.w);
    if(o.material<.5 && o.color.r>.78 && o.color.g>.72 && o.color.b<.7) {lit+=vec3(1.,.73,.35)*(1.-g.sun.w);}
    if(o.material<.5 && o.color.b>o.color.r*1.4 && o.color.g>.15) {lit+=vec3(.22,.13,.045)*(1.-g.sun.w);}
    if(o.material<1.5 && col.r<.2 && col.g<.2) {
        lit=mix(lit,lit*.72+sky_color(reflect(-view,n))*.13,g.effects.x);
    }
    let fog_amount=1.-exp(-pow(d/g.params.x,2.5)*2.4);
    lit=mix(lit,sky_color(normalize(o.world-g.camera.xyz)),clamp(fog_amount,0.,1.));
    lit=lit/(lit+vec3(.7))*1.32;
    return vec4(pow(max(lit,vec3(0.)),vec3(.85)),1.);
}
struct Screen { @builtin(position) p:vec4<f32>, @location(0) uv:vec2<f32> };
@vertex fn screen_vertex(@builtin(vertex_index) i:u32)->Screen {
    var p=array<vec2<f32>,3>(vec2(-1.,-1.),vec2(3.,-1.),vec2(-1.,3.));
    var o:Screen; o.p=vec4(p[i],0.,1.);o.uv=p[i];return o;
}
@fragment fn sky_fragment(o:Screen)->@location(0) vec4<f32> {
    let ray=normalize(g.forward.xyz+g.right.xyz*o.uv.x*g.params.y*.57735+g.up.xyz*o.uv.y*.57735);
    return vec4(sky_color(ray),1.);
}
@fragment fn rain_fragment(o:Screen)->@location(0) vec4<f32> {
    let p=(o.uv*.5+vec2(.5))*vec2(130.,60.);
    let column=floor(p.x+p.y*.13);
    let phase=fract(p.y+g.params.z*35.+hash(vec2(column,9.))*40.);
    let line=1.-smoothstep(.015,.06,abs(fract(p.x+p.y*.13)-.5));
    let tail=smoothstep(.58,.98,phase);
    return vec4(.68,.76,.82,line*tail*g.effects.x*.24);
}

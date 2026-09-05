"""Small shared, flat-normal prop meshes for instanced rendering (metres, Y up).

Vertex layout: position XYZ, normal XYZ, linear-ish muted RGB. Instance layout:
position XYZ + yaw; scale XYZ + reserved; tint RGB + material selector.
"""
import math
import numpy as np


class Builder:
    def __init__(self):
        self.vertices = []

    def tri(self, a, b, c, color):
        a, b, c = np.asarray(a), np.asarray(b), np.asarray(c)
        normal = np.cross(b-a, c-a)
        length = np.linalg.norm(normal)
        if length < 1e-9:
            return
        normal /= length
        for point in (a,b,c):
            self.vertices.append((*point, *normal, *color))

    def quad(self, a,b,c,d,color):
        self.tri(a,b,c,color)
        self.tri(a,c,d,color)

    def box(self, center, size, color):
        x,y,z = center
        w,h,d = np.array(size)/2
        p = [(x-w,y-h,z-d),(x+w,y-h,z-d),(x+w,y+h,z-d),(x-w,y+h,z-d),
             (x-w,y-h,z+d),(x+w,y-h,z+d),(x+w,y+h,z+d),(x-w,y+h,z+d)]
        for ids in [(0,3,2,1),(4,5,6,7),(0,4,7,3),(1,2,6,5),(3,7,6,2),(0,1,5,4)]:
            self.quad(*(p[i] for i in ids),color)

    def cone(self, center, radius, height, color, segments=9, top_radius=0):
        x,y,z = center
        for i in range(segments):
            a,b = math.tau*i/segments,math.tau*(i+1)/segments
            p=(x+math.cos(a)*radius,y,z+math.sin(a)*radius)
            q=(x+math.cos(b)*radius,y,z+math.sin(b)*radius)
            r=(x+math.cos(b)*top_radius,y+height,z+math.sin(b)*top_radius)
            s=(x+math.cos(a)*top_radius,y+height,z+math.sin(a)*top_radius)
            col=np.array(color)*(0.94+.06*math.sin(i*2.4))
            self.quad(q,p,s,r,col)
            self.tri((x,y,z),p,q,col)
            if top_radius:
                self.tri((x,y+height,z),r,s,col)

    def ellipsoid(self, center, scale, color, seed=1, rings=5, segments=9):
        rng=np.random.default_rng(seed)
        points=[]
        for j in range(rings+1):
            angle=math.pi*j/rings
            row=[]
            for i in range(segments):
                theta=math.tau*i/segments
                jitter=rng.uniform(.90,1.1) if j not in (0,rings) else 1
                row.append(np.array(center)+np.array(scale)*np.array([math.sin(angle)*math.cos(theta),math.cos(angle),math.sin(angle)*math.sin(theta)])*jitter)
            points.append(row)
        for j in range(rings):
            for i in range(segments):
                k=(i+1)%segments
                col=np.array(color)*rng.uniform(.92,1.05)
                self.quad(points[j][i],points[j][k],points[j+1][k],points[j+1][i],col)

    def finish(self):
        return np.asarray(self.vertices,dtype=np.float32).reshape(-1,9)


def _pine():
    b=Builder()
    b.cone((0,0,0),.32,6.8,(.27,.21,.15),7,.13)
    for i,(y,r,h) in enumerate([(1.7,2.2,3.4),(3.0,1.9,3.5),(4.4,1.5,3.0),(5.9,1.0,2.5)]):
        b.cone((.07*math.sin(i),y,.09*math.cos(i)),r,h,(.18+i*.013,.29+i*.018,.19+i*.014),11)
    return b.finish()


def _broadleaf(detailed=True):
    b=Builder()
    b.cone((0,0,0),.30,5.8,(.20,.16,.11),9,.06)
    rng=np.random.default_rng(712)
    # Open crowns made of small leaf sprays, not opaque polygonal balloons.
    for i in range(13):
        a=i*2.39996
        center=np.array([math.cos(a)*1.9,4.4+rng.uniform(0,2.5),math.sin(a)*1.9])
        _limb(b,(0,2.8,0),center,.065,(.23,.18,.12))
        for j in range(110 if detailed else 28):
            p=center+rng.normal(0,.59,3)
            color=np.array((.20,.31,.105))*rng.uniform(.72,1.3)
            size=1 if detailed else 1.8
            u=np.array([math.cos(j)*.18,.025,math.sin(j)*.18])*size
            v=np.array([-.045,.075,.04])*size
            b.quad(p-u,p-v,p+u,p+v,color)
            b.quad(p+v,p+u,p-v,p-u,color)
    return b.finish()


def _limb(b,a,c,r,color):
    a,c=np.array(a),np.array(c)
    direction=c-a
    u=np.cross(direction,[0,0,1.])
    if np.linalg.norm(u)<.001:u=np.cross(direction,[0,1.,0])
    u=u/np.linalg.norm(u)*r
    v=np.cross(direction,u);v=v/np.linalg.norm(v)*r
    for i in range(6):
        t=i*math.tau/6;s=(i+1)*math.tau/6
        p=u*math.cos(t)+v*math.sin(t);q=u*math.cos(s)+v*math.sin(s)
        b.quad(a+p,a+q,c+q*.75,c+p*.75,color)


def _walker(phase):
    b=Builder();skin=(.52,.32,.21);shirt=(.22,.32,.38);pants=(.09,.12,.16)
    b.ellipsoid((0,1.62,0),(.12,.16,.115),skin,4,5,8)
    b.ellipsoid((0,1.73,.015),(.125,.065,.12),(.10,.075,.045),3,3,8)
    b.cone((0,.93,0),.20,.49,shirt,8,.24)
    for side in (-1,1):
        swing=math.sin(phase)*side*.23
        hip=(side*.105,.96,0);knee=(side*.11,.52,swing*.5);foot=(side*.11,.09,swing)
        _limb(b,hip,knee,.085,pants);_limb(b,knee,foot,.065,pants)
        b.box((side*.11,.055,swing-.035),(.14,.11,.25),(.045,.04,.035))
        elbow=(side*.29,1.02,-swing*.6);hand=(side*.27,.83,-swing)
        _limb(b,(side*.21,1.38,0),elbow,.072,shirt);_limb(b,elbow,hand,.05,skin)
    return b.finish()


def _village():
    b=Builder()
    b.box((0,.04,0),(2.5,.14,28.2),(.45,.44,.40))
    for z in range(-13,14,2):
        b.box((-1.22,.08,z),(.15,.18,1.97),(.60,.58,.53))
    for z in (-8,8):
        b.box((3,.72,z),(5.5,.10,.10),(.29,.23,.15))
        b.box((3,1.12,z),(5.5,.10,.10),(.29,.23,.15))
        for x in (1,3,5):b.box((x,.65,z),(.13,1.3,.13),(.25,.20,.14))
    b.cone((1.6,0,-7),.08,5.5,(.20,.22,.20),8,.045)
    b.box((1.1,5.45,-7),(1.1,.11,.15),(.25,.26,.23))
    b.box((.65,5.35,-7),(.55,.12,.3),(.85,.81,.60))
    return b.finish()


def _rock():
    b=Builder()
    b.ellipsoid((0,.60,0),(1.4,1.0,1.2),(.47,.46,.39),seed=11,rings=3,segments=7)
    return b.finish()


def _house():
    b=Builder()
    wall=(.76,.68,.51)
    roof=(.29,.23,.19)
    trim=(.36,.30,.24)
    glass=(.15,.23,.25)
    b.box((0,.15,0),(5.2,.3,4.2),(.43,.43,.37))
    b.box((0,1.8,0),(5,3.3,4),wall)
    # Gable ridge along Z; roof eaves add a readable silhouette at distance.
    b.tri((-2.5,3.45,-2),(0,4.95,-2),(2.5,3.45,-2),wall)
    b.tri((2.5,3.45,2),(0,4.95,2),(-2.5,3.45,2),wall)
    b.quad((-2.8,3.38,-2.3),(-2.8,3.38,2.3),(0,5.06,2.3),(0,5.06,-2.3),roof)
    b.quad((0,5.06,-2.3),(0,5.06,2.3),(2.8,3.38,2.3),(2.8,3.38,-2.3),roof)
    b.box((1.2,4.55,.7),(.58,1.6,.58),(.48,.35,.28))
    b.box((1.2,5.37,.7),(.73,.14,.73),trim)
    b.box((0,1.24,2.03),(1.0,2.15,.10),trim)
    b.box((0,.34,2.35),(1.5,.25,.70),(.55,.52,.44))
    for x in (-1.6,1.6):
        for z in (-2.035,2.035):
            b.box((x,2.12,z),(1.03,1.17,.11),trim)
            b.box((x,2.12,z+math.copysign(.066,z)),(.85,.99,.035),glass)
            b.box((x,2.12,z+math.copysign(.09,z)),(.06,1.0,.04),(.68,.61,.46))
    for x in (-2.54,2.54):
        for z in (-.95,.95):
            b.box((x,2.0,z),(.10,1.22,1.08),trim)
            b.box((x+math.copysign(.06,x),2.,z),(.04,1.,.87),glass)
    # Individual tile courses, gutter and plaster plinth establish real scale.
    for y in range(12):
        x=.12+y*.23
        for side in (-1,1):
            b.box((side*x,5.06-x*.6+.025,0),(.055,.045,4.62),(.37,.25,.18))
    b.box((0,.35,0),(5.07,.30,4.07),(.49,.46,.39))
    return b.finish()


def _tower():
    b=Builder()
    for x in (-1.5,1.5):
        for z in (-1.5,1.5):
            b.box((x,4,z),(.28,8,.28),(.31,.33,.30))
    for y in (2,5,7.7):
        b.box((0,y,0),(3.4,.18,3.4),(.36,.36,.30))
    b.cone((0,8,0),2.4,3.2,(.55,.57,.48),12,2.4)
    b.cone((0,11.2,0),2.6,1.2,(.33,.37,.34),12)
    return b.finish()


def _grass():
    b=Builder()
    rng=np.random.default_rng(981)
    for i in range(42):
        a=i*2.39996
        x,z=math.cos(a)*rng.uniform(0,1.2),math.sin(a)*rng.uniform(0,1.2)
        w=.025+rng.random()*.025
        p=(x-w,0,z)
        q=(x+w,0,z)
        r=(x+math.sin(i)*.09,.30+.09*math.sin(i*2),z+.08)
        color=np.array((.28,.37,.13))*rng.uniform(.8,1.45)
        b.tri(p,q,r,color)
        b.tri(r,q,p,color)
    return b.finish()


def _post():
    b=Builder()
    b.box((0,.44,0),(.12,.88,.15),(.74,.72,.61))
    b.box((0,.69,0),(.13,.18,.16),(.19,.21,.20))
    b.box((0,.70,.085),(.07,.09,.02),(.93,.49,.17))
    return b.finish()


def prop_meshes():
    """Meshes are reusable across chunks; tree is the default world conifer."""
    pine,house=_pine(),_house()
    return {"tree":pine,"pine":pine,"broadleaf":_broadleaf(),"broadleaf_lod":_broadleaf(False),"rock":_rock(),
            "building":house,"house":house,"tower":_tower(),"grass":_grass(),"post":_post(),
            "village":_village(),**{f"walker{i}":_walker(i*math.tau/8) for i in range(8)}}


def instances_for_chunk(chunk, origin=(0,0)):
    """Subtract the renderer floating origin before converting to float32."""
    grouped={}
    for p in chunk.props:
        # Meshes already contain their material colors. A subtle per-instance tint
        # avoids multiplying foliage by an overly dark world categorical color.
        tint=.94+.06*math.sin(p.yaw*3)
        rgb=(tint,tint,tint)
        sx=sy=sz=p.scale
        if p.kind=='building':
            v=.5+.5*math.sin(p.x*17+p.z*3)
            palette=((1.,.96,.88),(.88,.91,.91),(.90,.77,.66),(.83,.86,.70))
            rgb=palette[int(v*3.99)]
            sx*=.90+.24*v;sy*=.85+.25*v;sz*=1.18-.26*v
        if p.kind.startswith('walker'):
            rgb=(.7+.3*abs(math.sin(p.z)),.75+.25*abs(math.cos(p.z)),.85)
        grouped.setdefault(p.kind,[]).append((p.x-origin[0],p.y,p.z-origin[1],p.yaw,
                                            sx,sy,sz,0,*rgb,0))
    return {kind:np.asarray(rows,dtype=np.float32).reshape(-1,12) for kind,rows in grouped.items()}

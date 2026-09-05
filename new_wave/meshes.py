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


def _broadleaf():
    b=Builder()
    b.cone((0,0,0),.35,4.6,(.31,.25,.18),8,.16)
    for i,(pos,scale) in enumerate([((0,5.6,0),(2.3,2.4,2.3)),((-1.7,4.9,.3),(1.5,1.7,1.7)),((1.5,5,.8),(1.8,1.8,1.7)),((.3,4.8,-1.5),(1.8,1.7,1.5))]):
        b.ellipsoid(pos,scale,(.29+i*.01,.35+i*.012,.17+i*.005),seed=i+1,rings=5,segments=10)
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
    for i in range(6):
        a=i*math.tau/6
        x,z=math.cos(a)*.20,math.sin(a)*.20
        w=.025
        p=(x-w,0,z)
        q=(x+w,0,z)
        r=(x+math.sin(i)*.09,.30+.09*math.sin(i*2),z+.08)
        color=(.54+i*.012,.47+i*.006,.26)
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
    return {"tree":pine,"pine":pine,"broadleaf":_broadleaf(),"rock":_rock(),
            "building":house,"house":house,"tower":_tower(),"grass":_grass(),"post":_post()}


def instances_for_chunk(chunk, origin=(0,0)):
    """Subtract the renderer floating origin before converting to float32."""
    grouped={}
    for p in chunk.props:
        # Meshes already contain their material colors. A subtle per-instance tint
        # avoids multiplying foliage by an overly dark world categorical color.
        tint=.94+.06*math.sin(p.yaw*3)
        grouped.setdefault(p.kind,[]).append((p.x-origin[0],p.y,p.z-origin[1],p.yaw,
                                            p.scale,p.scale,p.scale,0,tint,tint,tint,0))
    return {kind:np.asarray(rows,dtype=np.float32).reshape(-1,12) for kind,rows in grouped.items()}

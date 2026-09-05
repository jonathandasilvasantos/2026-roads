"""Rebuild the editable Enduro touring coupe with Blender (no external assets).

Run: /Applications/Blender.app/Contents/MacOS/Blender --background --python tools/build_assets.py
The GLB is assembled; NPZ contains triangle soup body and origin-centered wheel
templates in meters, Y up, -Z forward, with position/normal/linear RGB columns.
"""
from pathlib import Path
import json
import math
import bpy
import numpy as np
from mathutils import Vector

OUT = Path(__file__).resolve().parents[1] / "assets" / "new_wave"
OUT.mkdir(parents=True, exist_ok=True)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
MATERIALS = {}
BODY, WHEEL = [], []


def xyz(p):
    return (p[0], -p[2], p[1])


def mat(name, rgb, metallic=0.0, roughness=0.5):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*rgb, 1)
    m.use_nodes = True
    bs = m.node_tree.nodes.get("Principled BSDF")
    bs.inputs["Base Color"].default_value = (*rgb, 1)
    bs.inputs["Metallic"].default_value = metallic
    bs.inputs["Roughness"].default_value = roughness
    MATERIALS[name] = m
    return m


paint = mat("Burnt orange enamel", (.66, .185, .052), .35, .29)
dark = mat("Graphite rubber and trim", (.025, .031, .035), 0, .78)
glass = mat("Smoky petrol glass", (.085, .18, .215), .42, .2)
chrome = mat("Brushed warm alloy", (.56, .58, .55), .75, .31)
light = mat("Warm ivory headlamp", (.98, .88, .6), .2, .25)
red = mat("Ruby rear lenses", (.61, .033, .018), .1, .28)
amber = mat("Amber turn signals", (1.0, .38, .025), .1, .3)
stripe = mat("Sand rally pinstripe", (.85, .67, .39), .1, .45)


def finish(obj, name, material, bevel=0, group=BODY):
    obj.name = name
    obj.data.materials.append(material)
    bpy.context.view_layer.objects.active = obj
    if bevel:
        mod = obj.modifiers.new("Soft manufactured edges", "BEVEL")
        mod.width = bevel
        mod.segments = 1
        bpy.ops.object.modifier_apply(modifier=mod.name)
    # Keep surfaces planar; bevels provide predictable normals in all backends.
    for face in obj.data.polygons:
        face.use_smooth = False
    if not obj.data.uv_layers:
        obj.data.uv_layers.new(name="UVMap")
        for loop in obj.data.loops:
            p = obj.data.vertices[loop.vertex_index].co
            obj.data.uv_layers[0].data[loop.index].uv = (p.x * .25 + .5, p.y * .25 + .5)
    group.append(obj)
    return obj


def box(name, center, size, material, bevel=.018, group=BODY):
    bpy.ops.mesh.primitive_cube_add(size=1, location=xyz(center))
    obj = bpy.context.object
    obj.scale = (size[0], size[2], size[1])
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return finish(obj, name, material, bevel, group)


def mesh(name, vertices, faces, material, bevel=0, group=BODY):
    data = bpy.data.meshes.new(name)
    data.from_pydata([xyz(p) for p in vertices], [], faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    return finish(obj, name, material, bevel, group)


def loft(name, sections, material, bevel=.025):
    # Cross-sections: longitudinal position, half width, bottom, top.
    verts = [(x, y, z) for z, w, bottom, top in sections
             for x, y in [(-w, bottom), (w, bottom), (w, top), (-w, top)]]
    faces = [(3, 2, 1, 0)]
    for i in range(len(sections)-1):
        a, b = 4*i, 4*(i+1)
        faces.extend([(a+j, a+(j+1)%4, b+(j+1)%4, b+j) for j in range(4)])
    a = len(verts)-4
    faces.append((a, a+1, a+2, a+3))
    return mesh(name, verts, faces, material, bevel)


loft("Monocoque shoulder", [(-2.08,.80,.56,.85),(-1.77,.90,.5,.94),
     (-.67,.91,.51,1.03),(1.5,.9,.52,1.02),(2.03,.82,.6,.88)], paint)
box("Lower sill chassis", (0,.49,0), (1.55,.15,3.67), dark)
loft("Glazed cabin", [(-.72,.78,.99,1.03),(-.16,.72,1.0,1.55),
     (.83,.72,1,1.55),(1.62,.78,1.0,1.03)], glass, .009)
box("Floating painted roof", (0,1.565,.33), (1.48,.058,1.07), paint, .035)
# Window pillars follow the cabin slope; side surfaces are real planar meshes.
for side in (-1, 1):
    x = side * .736
    box("B pillar", (x,1.285,.35), (.045,.53,.066), dark, .006)
    box("Window waist trim", (side*.80,1.035,.43), (.035,.034,2.0), chrome, .004)
    box("Door handle", (side*.921,.94,.51), (.035,.03,.18), chrome, .006)
    box("Sill protective strip", (side*.911,.67,.10), (.026,.045,3.6), dark, .007)
    box("Rally pinstripe", (side*.915,.79,.16), (.012,.016,3.53), stripe, .002)
    box("Mirror stalk", (side*.93,1.09,-.57), (.17,.036,.04), dark, .006)
    box("Painted mirror", (side*1.005,1.12,-.57), (.19,.105,.20), paint, .028)
    box("Mirror glass", (side*1.005,1.122,-.462), (.13,.063,.014), chrome, .004)
    for z in (-1.35,1.35):
        # Arch lips frame the tires without adding complex collision geometry.
        for angle in range(0,180,30):
            a=math.radians(angle+15)
            o=box("Fender flare", (side*.91,.36+.425*math.sin(a),z+.425*math.cos(a)),
                  (.085,.065,.23), paint, .012)
            o.rotation_euler[0]=math.pi/2-a
    # Front and rear windshield surround strips.
    for z0,y0,z1,y1 in [(-.72,1.035,-.16,1.55),(.83,1.55,1.62,1.035)]:
        mesh("Sloped window pillar",[(side*.79,y0,z0),(side*.72,y1,z1),
             (side*.67,y1,z1),(side*.74,y0,z0)],[(0,1,2,3)],paint)
box("Hood center", (0,.955,-1.39), (1.56,.028,1.03), paint,.022)
for side in (-1,1):
    box("Hood seam",(side*.73,.976,-1.34),(.009,.003,.96),dark,0)
    box("Rectangular headlamp",(side*.55,.785,-2.091),(.39,.16,.04),light,.017)
    box("Amber front indicator",(side*.78,.66,-2.071),(.16,.065,.04),amber,.007)
    box("Tail lamp",(side*.55,.79,2.036),(.44,.12,.038),red,.008)
    box("Rear amber lens",(side*.77,.79,2.039),(.08,.12,.04),amber,.006)
box("Recessed grille",(0,.75,-2.1),(.57,.17,.043),dark,.014)
for y in (.70,.75,.80):
    box("Grille slat",(0,y,-2.126),(.5,.013,.018),chrome,.002)
box("Front impact bumper",(0,.54,-2.07),(1.83,.16,.17),dark,.034)
box("Rear impact bumper",(0,.55,2.045),(1.8,.15,.15),dark,.027)
box("Rear plate recess",(0,.745,2.05),(.33,.12,.03),dark,.009)
box("Rear spoiler",(0,1.053,1.69),(1.6,.05,.19),dark,.016)
box("Exhaust outlet",(.60,.425,2.08),(.11,.075,.20),chrome,.014)

# Separate wheel template: axle on X, origin at hub. Ring profile forms tread
# shoulders and sidewalls in one manifold mesh, with simple inset rally alloy.
profile=[(-.12,.24),(-.115,.32),(-.085,.36),(.085,.36),(.115,.32),(.12,.24)]
n=16
verts=[(x,r*math.sin(2*math.pi*i/n),r*math.cos(2*math.pi*i/n)) for x,r in profile for i in range(n)]
faces=[((j+1)*n+i,(j+1)*n+(i+1)%n,j*n+(i+1)%n,j*n+i) for j in range(len(profile)-1) for i in range(n)]
mesh("All terrain tire",verts,faces,dark,group=WHEEL)
for side in (-1,1):
    x=side*.122
    verts=[(x,0,0)]+[(x,.245*math.sin(2*math.pi*i/n),.245*math.cos(2*math.pi*i/n)) for i in range(n)]
    faces=[(0,1+i,1+(i+1)%n) if side<0 else (0,1+(i+1)%n,1+i) for i in range(n)]
    mesh("Rally alloy disc",verts,faces,chrome,group=WHEEL)
    for i in range(6):
        a=2*math.pi*i/6
        box("Alloy ventilation slot",(x+side*.002,.155*math.sin(a),.155*math.cos(a)),
            (.003,.052,.064),dark,0,WHEEL)
    box("Hub cap",(x+side*.006,0,0),(.02,.09,.09),chrome,0,WHEEL)


def triangles(objects):
    rows=[]
    for obj in objects:
        data=obj.data
        data.calc_loop_triangles()
        normal_matrix=obj.matrix_world.to_3x3().inverted().transposed()
        color=tuple(obj.data.materials[0].diffuse_color)[:3]
        for tri in data.loop_triangles:
            normal=(normal_matrix @ tri.normal).normalized()
            normal=(normal.x,normal.z,-normal.y)
            for idx in tri.vertices:
                p=obj.matrix_world @ data.vertices[idx].co
                rows.append((p.x,p.z,-p.y,*normal,*color))
    return np.asarray(rows,dtype=np.float32)


bpy.context.view_layer.update()
body_array, wheel_array=triangles(BODY),triangles(WHEEL)
np.savez_compressed(OUT / "roamer.npz",body=body_array,wheel=wheel_array)
# Assemble wheel instances for Blender editing and engine-independent GLB.
for obj in WHEEL:
    for x in (-.86,.86):
        for z in (-1.35,1.35):
            instance=obj.copy()
            instance.data=obj.data
            bpy.context.collection.objects.link(instance)
            instance.location += Vector(xyz((x,.36,z)))
            instance.name=f"Wheel_{'L' if x<0 else 'R'}_{'front' if z<0 else 'rear'}_{obj.name}"
    bpy.data.objects.remove(obj,do_unlink=True)
bpy.context.scene.unit_settings.system="METRIC"
bpy.context.scene.unit_settings.scale_length=1
bpy.context.preferences.filepaths.save_version=0
bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "roamer.blend"))
bpy.ops.export_scene.gltf(filepath=str(OUT / "roamer.glb"),export_format="GLB",export_yup=True)
report={}
for name,arr in [("body",body_array),("wheel",wheel_array)]:
    assert np.isfinite(arr).all()
    assert np.allclose(np.linalg.norm(arr[:,3:6],axis=1),1,atol=1e-5)
    report[name]={"triangles":len(arr)//3,"bounds_min":arr[:,:3].min(axis=0).tolist(),
                  "bounds_max":arr[:,:3].max(axis=0).tolist()}
print("ENDURO_ASSET_VALIDATION "+json.dumps(report))

"""Build an editable Blender library and engine-ready exports of roadside assets.

Run with Blender --background --python tools/build_environment.py.
Runtime coordinates are metres, Y up; Blender source and GLB use their native
coordinate conventions. Each named object is an origin-centred library asset.
"""
from pathlib import Path
import sys
import bpy
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from new_wave.meshes import prop_meshes

out=ROOT/'assets/new_wave'
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
meshes=prop_meshes()
material=bpy.data.materials.new('Authored linear vertex albedo')
material.use_nodes=True
nodes=material.node_tree.nodes
color=nodes.new('ShaderNodeVertexColor');color.layer_name='Color'
material.node_tree.links.new(color.outputs['Color'],nodes.get('Principled BSDF').inputs['Base Color'])
nodes.get('Principled BSDF').inputs['Roughness'].default_value=.72
for name,vertices in meshes.items():
    mesh=bpy.data.meshes.new(name)
    positions=[(float(v[0]),float(-v[2]),float(v[1])) for v in vertices]
    mesh.from_pydata(positions,[],[(i,i+1,i+2) for i in range(0,len(positions),3)])
    mesh.materials.append(material)
    colors=mesh.color_attributes.new(name='Color',type='FLOAT_COLOR',domain='CORNER')
    uv=mesh.uv_layers.new(name='UVMap')
    for i,v in enumerate(vertices):
        colors.data[i].color=(*map(float,v[6:9]),1.)
        uv.data[i].uv=(float(v[0])*.25,float(v[1]+v[2])*.25)
    obj=bpy.data.objects.new(name,mesh)
    bpy.context.collection.objects.link(obj)
    obj['units']='metres; pivot at ground; independent reusable asset'
    obj['collision']='simple world proxy; foliage and decorative parts non-solid'
    assert np.isfinite(vertices).all() and len(vertices)%3==0
bpy.ops.wm.save_as_mainfile(filepath=str(out/'roadside.blend'))
bpy.ops.export_scene.gltf(filepath=str(out/'roadside.glb'),export_format='GLB',export_yup=True)
np.savez_compressed(out/'roadside.npz',**meshes)
print('ROADSIDE_EXPORT', {k:len(v)//3 for k,v in meshes.items()})

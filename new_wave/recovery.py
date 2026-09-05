"""Find a nearby supported, unobstructed position for spawn and recovery."""
import math


def clear_position(world, x, z, radius=1.7, prefer_road=True):
    fallback=None
    for distance in (0,4,8,12,20,32,48,64):
        for i in range(1 if distance==0 else 24):
            angle=i*math.tau/24
            px,pz=x+math.cos(angle)*distance,z+math.sin(angle)*distance
            y=world.surface_height(px,pz)
            if not math.isfinite(y):continue
            if any(math.hypot(px-p.x,pz-p.z)<p.radius+radius for p in world.nearby_colliders(px,pz,16)):continue
            if max(abs(world.surface_height(px+2,pz)-y),abs(world.surface_height(px,pz+2)-y))>1.2:continue
            if world.road_distance(px,pz)<5 or not prefer_road:return px,pz
            if fallback is None:fallback=(px,pz)
    return fallback or (x,z)

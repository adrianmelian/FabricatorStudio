import maya.mel as mel
import maya.cmds as cmds
import pymel.core as pm

# CLean Skin = Remove history and freeze transforms on a bound mesh
def clean_selected_skin():
    if cmds.ls(sl=1) :
        selected_geo = [a for a in cmds.filterExpand( ex=True, sm=12 )]
        for geo in selected_geo:
            clean_skin(geo)
    else :
        cmds.warning('Please select a skinned mesh to cleanup. Aborting')
        return

def clean_skin(geo_a):
    # Turn off bind poses
    bind_poses = cmds.ls(type='dagPose')
    if bind_poses:
        cmds.delete(bind_poses)

    # Get original skin cluster and influences
    skin_a = mel.eval('findRelatedSkinCluster ' + geo_a)
    if not skin_a:
        return

    # Duplicate geo and freeze transforms
    geo_b = pm.duplicate(geo_a, rr=1)[0]
    for attr in ['tx', 'ty', 'tz', 'rx', 'ry', 'rz', 'sx', 'sy', 'sz', 'v']:
        pm.PyNode("{}.{}".format(geo_b, attr)).unlock()
    pm.makeIdentity(geo_b, apply=1, t=1, r=1, s=1, n=0)

    # Delete intermediate nodes
    clean_intermediate_children(geo_b.longName())

    # Use same influences to bind new geo
    influences = cmds.skinCluster(geo_a, q=1, inf=1)
    skin_b = cmds.skinCluster(influences, geo_b.longName(), tsb=1, omi=0)
    cmds.select(cl=1)

    # Copy the skin weights
    cmds.copySkinWeights( ss=skin_a, ds=str(skin_b[0]), nm=1, sa='closestPoint', ia=['oneToOne','name'] )

    # Cleanup
    cmds.delete(geo_a)

    # clean up the name here
    # to prevent renaming with full path
    # such as head -> parent|child|head
    original_name = geo_a
    cleaned_name = geo_a
    if '|' in cleaned_name:
        last_item = cleaned_name.split('|')[-1]
        if last_item:
            cleaned_name = last_item

    cmds.rename(geo_b.longName(), cleaned_name)
    cmds.select(original_name)

def clean_intermediate_children(node, debug=False):
    if debug:
        print 'deleting intermediate children'
    children = cmds.listRelatives(node, c=1, f=1)
    for child in children :
        delete_intermediate_mesh(child)

def delete_intermediate_mesh(mesh, debug=False):
    if cmds.getAttr('%s.intermediateObject'%mesh) == 1:
        mesh_connections = cmds.listConnections(mesh)
        if not mesh_connections:
            if debug:
                print 'deleting %s'%mesh
            cmds.delete(mesh)

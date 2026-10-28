from typing import Optional
from .path import UniPath
from .util import remove_prefix_if_present
from .ff_avtx import image_load, ddsc_write_to_dds, ddsc_write_to_png, ddsc_clean
from .ff_adf import *
from .ff_adf_amf import *
import pygltflib as pyg
import copy

def _get_or_none(index, list_data):
    if index < len(list_data):
        return list_data[index]
    else:
        return None


class Deca3dMatrix:
    def __init__(self, col_major=None, row_major=None):
        assert not ((col_major is not None) and (row_major is not None))

        self.data = None

        if col_major is not None:
            self.data = np.array(col_major).reshape(4, 4).transpose()

        if row_major is not None:
            self.data = np.array(row_major).reshape(4, 4)

    def prep(self):
        if self.data is None:
            self.data = np.identity(4)

    def translate(self, vec3):
        self.prep()
        self.data[0:3, 3] += vec3

    def col_major_list(self):
        if self.data is None:
            return None
        else:
            return list(self.data.transpose().reshape((-1,)))

    def row_major_list(self):
        if self.data is None:
            return None
        else:
            return list(self.data.reshape((-1,)))

    @staticmethod
    def matmul(a, b):
        if a is None or a.data is None:
            return b
        if b is None or b.data is None:
            return a

        mat = Deca3dMatrix()
        mat.data = np.matmul(a.data, b.data)
        return mat


# COTWed DECA, 2026-10-18 : two files can carry the same name when their name hashes collide (hash32 of the path). In
# COTW an untyped texture is named models/hp_characters/animals/eu_hare/internal_organs/
# eu_hare_internal_left_fore_phalanges.meshc like the real mesh, and nodes_where_match(...)[0] returned the texture,
# so the export of the hare model failed. Pick the node whose type fits.
TYPES_ADF_NODE = {'adf', 'adf0', 'adf5', 'adfb'}
TYPES_TEXTURE_NODE = {'avtx', 'dds'}
TYPES_HAVOK_NODE = {'h2014'}


def node_of_type(vfs, v_path, types):
    """Nodes of v_path whose file type is in types first, then the others (empty list if v_path is not mapped)."""
    nodes = vfs.nodes_where_match(v_path=v_path)
    def kind(n):
        t = n.file_type.decode('utf-8', 'replace') if isinstance(n.file_type, bytes) else n.file_type
        return 0 if t in types else 1
    return sorted(nodes, key=kind)


class Deca3dDatabase:
    def __init__(self, vfs: VfsDatabase, resource_prefix_abs, resource_prefix_uri, flat_file_layout, texture_format):
        self.vfs = vfs
        self.adf_db = AdfDatabase(vfs)
        self.resource_prefix_abs = resource_prefix_abs
        self.resource_prefix_uri = resource_prefix_uri
        self.map_vpath_to_texture = {}
        self.map_vpath_to_meshc = {}
        self.map_vpath_to_modelc = {}
        self.map_vpath_to_hk_skeleton = {}
        self.flat_file_layout = flat_file_layout
        self.texture_format = texture_format

    def gltf_add_texture(self, gltf, v_path):
        item = self.map_vpath_to_texture.get(v_path, Deca3dTexture(v_path))
        self.map_vpath_to_texture[v_path] = item
        return item.add_to_gltf(self.vfs, self.adf_db, self, gltf, self.texture_format)

    def gltf_add_meshc(self, gltf, v_path):
        item = self.map_vpath_to_meshc.get(v_path, Deca3dMeshc(v_path))
        self.map_vpath_to_meshc[v_path] = item
        return item.add_to_gltf(self.vfs, self.adf_db, self, gltf)

    def gltf_add_modelc(self, gltf, v_path, material_properties=None):
        item = self.map_vpath_to_modelc.get(v_path, Deca3dModelc(v_path, material_properties))
        self.map_vpath_to_modelc[v_path] = item
        return item.add_to_gltf(self.vfs, self.adf_db, self, gltf)

    def gltf_add_hk_skeleton(self, gltf, v_path, scene):
        item = self.map_vpath_to_hk_skeleton.get(v_path, Deca3dHkSkeleton(v_path, scene))
        self.map_vpath_to_hk_skeleton[v_path] = item
        return item.add_to_gltf(self.vfs, self.adf_db, self, gltf)

    def gltf_finalize_mesh(self, mesh_org: pyg.Mesh, material_map=None):
        mesh = copy.deepcopy(mesh_org)
        p: pyg.Primitive
        for p in mesh.primitives:
            p.material = material_map.get(p.material, None)

        return mesh


class Deca3dTexture:
    def __init__(self, v_path):
        self.v_path = v_path
        self.gltf_id = None
        self.texture_saved = False

    def add_to_gltf(self, vfs: VfsDatabase, adf_db, db: Deca3dDatabase, gltf: pyg.GLTF2, texture_format):
        if not self.texture_saved:
            vfs.logger.log('Texture:Setup: {}'.format(self.v_path))
            # dump textures
            texture_fn_uri = None
            if len(self.v_path) > 0:
                texture_nodes = node_of_type(vfs, self.v_path, TYPES_TEXTURE_NODE)
                if len(texture_nodes) > 0:
                    texture_fn = self.v_path.decode('utf-8')
                    if not texture_fn.endswith(texture_format):
                        texture_fn += '.' + texture_format

                    if db.flat_file_layout:
                        texture_fn = texture_fn.replace('/', '_')
                    texture_fn_uri = UniPath.join(db.resource_prefix_uri, texture_fn)
                    texture_fn_absolute = UniPath.join(db.resource_prefix_abs, texture_fn)

                    if not UniPath.isfile(texture_fn_absolute):
                        texture_node = texture_nodes[0]
                        ddsc = image_load(vfs, texture_node, save_raw_data=True)

                        if ddsc_clean(ddsc):
                            vfs.logger.warning('WARNING: {}: missing high resolution data'.format(self.v_path))

                        os.makedirs(UniPath.dirname(texture_fn_absolute), exist_ok=True)

                        if texture_fn_absolute.endswith('png'):
                            ddsc_write_to_png(ddsc, texture_fn_absolute)
                        elif texture_fn_absolute.endswith('ddsc') or texture_fn_absolute.endswith('dds'):
                            ddsc_write_to_dds(ddsc, texture_fn_absolute)
                        else:
                            vfs.logger.log('ERROR: {}: Unhandled Texture format: {}'.format(self.v_path, texture_format))

                else:
                    vfs.logger.log('WARNING: Missing Texture file: {}'.format(self.v_path))

            # Setup GLTF.Image
            if texture_fn_uri is not None:
                gltf_image = pyg.Image()
                gltf_image.uri = texture_fn_uri
                image_idx = len(gltf.images)
                gltf.images.append(gltf_image)

                gltf_texture = pyg.Texture()
                gltf_texture.source = image_idx
                # gltf_texture.sampler
                # gltf_texture.extras

                texture_index = len(gltf.textures)
                gltf.textures.append(gltf_texture)

                self.gltf_id = texture_index

            self.texture_saved = True

        return self.gltf_id


def load_meshc(vfs: VfsDatabase, adf_db: AdfDatabase, v_path):
    """AmfMeshHeader and AmfMeshBuffers of a .meshc, with the buffers of its .hrmeshc appended, before
    amf_meshc_reformat (the raw vertex formats of the file). Shared by the export and the audits of COTWed DECA."""
    mesh_adf = adf_db.read_node(vfs, node_of_type(vfs, v_path, TYPES_ADF_NODE)[0])
    assert len(mesh_adf.table_instance) == 2
    # 0xea60065d - gz/hp, 0x7A2C9B73 - rg2, 0x6f841426 - hp (also)
    assert mesh_adf.table_instance[0].type_hash in {0xea60065d, 0x7A2C9B73, 0x6f841426}
    # 0x67b3a453 - gz, 0xe6834477 - hp, 0x0E1C0800 - rg2
    assert mesh_adf.table_instance[1].type_hash in {0x67b3a453, 0xe6834477, 0x0E1C0800}
    mesh_header = AmfMeshHeader(
        mesh_adf,
        mesh_adf.table_instance_full_values[0],
        merged_buffers=mesh_adf.table_instance[0].type_hash in {0x7A2C9B73})
    mesh_buffers = AmfMeshBuffers(
        mesh_adf,
        mesh_adf.table_instance_full_values[1],
        merged_buffers=mesh_adf.table_instance[1].type_hash in {0x0E1C0800})

    hrmesh_vpath = mesh_header.highLodPath
    hrmesh_vpath2 = remove_prefix_if_present(b'intermediate/', hrmesh_vpath)
    hrmesh_node = None

    hrmesh_nodes = node_of_type(vfs, hrmesh_vpath, TYPES_ADF_NODE)
    if hrmesh_nodes:
        hrmesh_node = hrmesh_nodes[0]
    elif hrmesh_vpath2 is not None:
        hrmesh_nodes = node_of_type(vfs, hrmesh_vpath2, TYPES_ADF_NODE)
        if hrmesh_nodes:
            hrmesh_node = hrmesh_nodes[0]

    if hrmesh_node is not None:
        hrmesh_adf = adf_db.read_node(vfs, hrmesh_node)
        assert len(hrmesh_adf.table_instance) == 1
        # assert hrmesh_adf.table_instance[0].type_hash == 0x67b3a453
        hrmesh_buffers = AmfMeshBuffers(
            hrmesh_adf,
            hrmesh_adf.table_instance_full_values[0],
            merged_buffers=mesh_adf.table_instance[1].type_hash in {0x0E1C0800})
        mesh_buffers.indexBuffers = mesh_buffers.indexBuffers + hrmesh_buffers.indexBuffers
        mesh_buffers.vertexBuffers = mesh_buffers.vertexBuffers + hrmesh_buffers.vertexBuffers
    return mesh_header, mesh_buffers


class Deca3dMeshc:
    # The mesh is never stored in the gltf directly
    # The mesh stores it's accessors, buffer views, and buffers in the gltf when used
    # The mesh writes out it's support files (buffers) when used
    def __init__(self, v_path):
        self.v_path = v_path
        self.meshes = None

    def add_to_gltf(self, vfs: VfsDatabase, adf_db: AdfDatabase, db: Deca3dDatabase, gltf: pyg.GLTF2):
        if self.meshes is None:
            vfs.logger.log('Setup Meshc: {}'.format(self.v_path))
            mesh_header, mesh_buffers = load_meshc(vfs, adf_db, self.v_path)

            # reformat buffers for GLTF
            amf_meshc_reformat(mesh_header, mesh_buffers)

            # process buffers
            index_buffer_fns = []
            index_buffer_ids = []
            vertex_buffer_fns = []
            vertex_buffer_ids = []

            for index, buffer in enumerate(mesh_buffers.indexBuffers):
                fn = self.v_path.decode('utf-8') + '.buffer_index_{:03}.bin'.format(index)
                if db.flat_file_layout:
                    fn = fn.replace('/', '_')
                fn_uri = UniPath.join(db.resource_prefix_uri, fn)
                fn_abs = UniPath.join(db.resource_prefix_abs, fn)
                if not UniPath.isfile(fn_abs):
                    os.makedirs(UniPath.dirname(fn_abs), exist_ok=True)
                    with open(fn_abs, 'wb') as f:
                        f.write(buffer.data)
                index_buffer_fns.append(fn_abs)
                index_buffer_ids.append(len(gltf.buffers))
                gltf.buffers.append(pyg.Buffer(uri=fn_uri, byteLength=os.stat(fn_abs).st_size))

            for index, buffer in enumerate(mesh_buffers.vertexBuffers):
                fn = self.v_path.decode('utf-8') + '.buffer_vertex_{:03}.bin'.format(index)
                if db.flat_file_layout:
                    fn = fn.replace('/', '_')
                fn_uri = UniPath.join(db.resource_prefix_uri, fn)
                fn_abs = UniPath.join(db.resource_prefix_abs, fn)
                if not UniPath.isfile(fn_abs):
                    os.makedirs(UniPath.dirname(fn_abs), exist_ok=True)
                    with open(fn_abs, 'wb') as f:
                        f.write(buffer.data)
                vertex_buffer_fns.append(fn_abs)
                vertex_buffer_ids.append(len(gltf.buffers))
                gltf.buffers.append(pyg.Buffer(uri=fn_uri, byteLength=os.stat(fn_abs).st_size))

            self.meshes = {}
            lod_group: AmfLodGroup
            for lod_group in mesh_header.lodGroups:
                vfs.logger.log('Exporting {}: LOD {}: Started'.format(self.v_path, lod_group.lodIndex))

                meshes = []
                mesh: AmfMesh
                for mesh in lod_group.meshes:
                    # Setup bufferViews: APEX streams are GLTF2's buffer views
                    index_stream_ids = []
                    buffer_view = pyg.BufferView()
                    buffer_view.buffer = index_buffer_ids[mesh.indexBufferIndex]
                    buffer_view.byteLength = mesh.indexCount * mesh.indexBufferStride
                    buffer_view.byteOffset = mesh.indexBufferOffset
                    # buffer_view.byteStride = mesh.indexBufferStride  # commented out accessor handles size
                    buffer_view.target = 34963  # ELEMENT_ARRAY_BUFFER #TODO
                    index_stream_ids.append(len(gltf.bufferViews))
                    gltf.bufferViews.append(buffer_view)

                    vertex_stream_ids = []
                    for idx in range(len(mesh.vertexBufferIndices)):
                        buffer_view = pyg.BufferView()
                        buffer_view.buffer = vertex_buffer_ids[mesh.vertexBufferIndices[idx]]
                        buffer_view.byteLength = mesh.vertexCount * mesh.vertexStreamStrides[idx]
                        buffer_view.byteOffset = mesh.vertexStreamOffsets[idx]
                        buffer_view.byteStride = mesh.vertexStreamStrides[idx]
                        buffer_view.target = 34962  # ARRAY_BUFFER #TODO
                        vertex_stream_ids.append(len(gltf.bufferViews))
                        gltf.bufferViews.append(buffer_view)

                    # Setup GLTF accessors
                    possibly_skin = False
                    accessors_streams = []
                    accessors_stream_map = {}
                    stream_attr: AmfStreamAttribute
                    for stream_attr in mesh.streamAttributes:
                        accessor = pyg.Accessor()
                        accessor.bufferView = vertex_stream_ids[stream_attr.streamIndex]
                        accessor.byteOffset = stream_attr.streamOffset
                        accessor.count = mesh.vertexCount
                        if stream_attr.format[1] == b'AmfFormat_R16G16B16_SNORM':
                            accessor.type = "VEC3"
                            accessor.componentType = pyg.SHORT
                            accessor.normalized = True
                        elif stream_attr.format[1] == b'AmfFormat_R16G16_SNORM':
                            accessor.type = "VEC2"
                            accessor.componentType = pyg.SHORT
                            accessor.normalized = True
                        elif stream_attr.format[1] == b'AmfFormat_R16G16_UNORM':
                            accessor.type = "VEC2"
                            accessor.componentType = pyg.UNSIGNED_SHORT
                            accessor.normalized = True
                        elif stream_attr.format[1] == b'AmfFormat_R16_SNORM':
                            accessor.type = "SCALAR"
                            accessor.componentType = pyg.SHORT
                            accessor.normalized = True
                        elif stream_attr.format[1] == b'AmfFormat_R32_UINT':
                            accessor.type = "SCALAR"
                            accessor.componentType = pyg.UNSIGNED_INT
                        elif stream_attr.format[1] == b'AmfFormat_R8G8B8A8_UNORM':
                            accessor.type = "VEC4"
                            accessor.componentType = pyg.UNSIGNED_BYTE
                            accessor.normalized = True
                        elif stream_attr.format[1] == b'AmfFormat_R8G8B8A8_UINT':
                            accessor.type = "VEC4"
                            accessor.componentType = pyg.UNSIGNED_BYTE
                        elif stream_attr.format[1] == b'AmfFormat_R16G16B16A16_SINT':
                            accessor.type = "VEC4"
                            accessor.componentType = pyg.SHORT
                        elif stream_attr.format[1] == b'AmfFormat_R32_UNIT_VEC_AS_FLOAT':
                            accessor.type = "SCALAR"
                            accessor.componentType = pyg.FLOAT
                        elif stream_attr.format[1] == b'AmfFormat_R32_R8G8B8A8_UNORM_AS_FLOAT':
                            accessor.type = "SCALAR"
                            accessor.componentType = pyg.FLOAT
                        elif stream_attr.format[1] == b'AmfFormat_R32_FLOAT':
                            accessor.type = "SCALAR"
                            accessor.componentType = pyg.FLOAT
                        elif stream_attr.format[1] == b'AmfFormat_R32G32_FLOAT':
                            accessor.type = "VEC2"
                            accessor.componentType = pyg.FLOAT
                        elif stream_attr.format[1] == b'AmfFormat_R32G32B32_FLOAT':
                            accessor.type = "VEC3"
                            accessor.componentType = pyg.FLOAT
                        elif stream_attr.format[1] == b'AmfFormat_R32G32B32A32_FLOAT':
                            accessor.type = "VEC4"
                            accessor.componentType = pyg.FLOAT
                        elif stream_attr.format[1] == b'DecaFormat_R32G32B32A32_FLOAT_P1':
                            accessor.type = "VEC4"
                            accessor.componentType = pyg.FLOAT
                        elif stream_attr.format[1] == b'DecaFormat_R32G32B32A32_FLOAT_N1':
                            accessor.type = "VEC4"
                            accessor.componentType = pyg.FLOAT
                        else:
                            raise NotImplementedError('Unknown Stream Format: {}'.format(stream_attr.format[1]))

                        if stream_attr.usage[1] == b'AmfUsage_Position':
                            accessor.min = list(stream_attr.min.astype(dtype=np.double))
                            accessor.max = list(stream_attr.max.astype(dtype=np.double))

                        if stream_attr.usage[1] in {b'AmfUsage_BoneIndex', b'AmfUsage_BoneWeight'}:
                            possibly_skin = True

                        # add accessor
                        accessors_stream_idx = len(gltf.accessors)
                        gltf.accessors.append(accessor)
                        accessors_streams.append(accessors_stream_idx)
                        asl = accessors_stream_map.get(stream_attr.usage[1], [])
                        asl.append(accessors_stream_idx)
                        accessors_stream_map[stream_attr.usage[1]] = asl

                    do_skin = \
                        mesh.meshProperties is not None and \
                        mesh.meshProperties.get('IsSkinnedMesh', 0) == 1 and \
                        len(mesh.boneIndexLookup) > 0

                    do_skin = do_skin or possibly_skin

                    submeshes = []
                    submesh: AmfSubMesh
                    for submesh in mesh.subMeshes:
                        # submesh index accessor
                        accessor = pyg.Accessor()
                        accessor.bufferView = index_stream_ids[0]
                        accessor.byteOffset = submesh.indexStreamOffset
                        accessor.count = submesh.indexCount
                        accessor.type = "SCALAR"
                        accessor.componentType = 5123  # UNSIGNED_SHORT

                        # add accessor
                        accessor_idx = len(gltf.accessors)
                        gltf.accessors.append(accessor)

                        prim = pyg.Primitive()
                        prim.indices = accessor_idx

                        prim.attributes = pyg.Attributes(
                            POSITION=_get_or_none(0, accessors_stream_map.get(b'AmfUsage_Position', [])),
                            TEXCOORD_0=_get_or_none(0, accessors_stream_map.get(b'AmfUsage_TextureCoordinate', [])),
                            TEXCOORD_1=_get_or_none(1, accessors_stream_map.get(b'AmfUsage_TextureCoordinate', [])),
                            NORMAL=_get_or_none(0, accessors_stream_map.get(b'AmfUsage_Normal', [])),
                            TANGENT=_get_or_none(0, accessors_stream_map.get(b'AmfUsage_Tangent', [])),
                            COLOR_0=_get_or_none(0, accessors_stream_map.get(b'AmfUsage_Color', [])),
                            JOINTS_0=_get_or_none(0, accessors_stream_map.get(b'AmfUsage_BoneIndex', [])),
                            WEIGHTS_0=_get_or_none(0, accessors_stream_map.get(b'AmfUsage_BoneWeight', [])),
                        )

                        # !!!!!! TEMPORARILY SAVE STRING HERE TO BE REPLACE BY MATERIAL FROM MODEL
                        prim.material = submesh.subMeshId

                        gltf_mesh = pyg.Mesh()
                        gltf_mesh.name = submesh.subMeshId.decode('utf-8')
                        gltf_mesh.primitives.append(prim)
                        submeshes.append(gltf_mesh)

                    meshes.append((submeshes, do_skin))
                self.meshes[lod_group.lodIndex] = meshes
                vfs.logger.log('Exporting {}: LOD {}: Complete'.format(self.v_path, lod_group.lodIndex))
        return self.meshes


# COTWed DECA, 2026-10-22 : render blocks whose first texture slots are [0] diffuse (_dif), [1] normal (_nrm),
# [2] MPM (_mpm), measured on the 39 937 materials of the 24 278 COTW models (audit 16-materiaux, slots named by the
# end of the texture file names : 100 % in each slot). DECA only handled GeneralR2, Character and CarPaint ;
# CharacterSkin (animals, 10 300 models), Window, WeaponCamo (weapons ; [4] is the camo RGB mask, not emissive),
# Lens, CarLight and CarPaintMM were exported without material.
RENDER_BLOCKS_DIF_NRM_MPM = {
    b'GeneralR2', b'Character', b'CarPaint',
    b'CharacterSkin', b'Window', b'WeaponCamo', b'Lens', b'CarLight', b'CarPaintMM',
}
RENDER_BLOCKS_TRANSPARENT = {b'Window', b'Lens'}


def material_finish(gltf_material, material):
    """Double-sided and transparency, from the material attributes and render block."""
    attrs = material.attributes or {}
    if attrs.get('DoubleSided', False) or attrs.get('TwoSided', False):
        gltf_material.doubleSided = True
    if material.renderBlockId in RENDER_BLOCKS_TRANSPARENT or attrs.get('AlphaBlending', False) \
            or attrs.get('AlphaBlend', False):
        gltf_material.alphaMode = 'BLEND'
    elif attrs.get('AlphaTest', False):
        gltf_material.alphaMode = 'MASK'


class Deca3dModelc:
    # The model stores it's mesh with material info in the gltf when instantiated in node, only once.
    # The model stores it's material, texture info, ... in the gltf when used
    # The model writes out it's support files (textures) when used
    # The model creates a copy of the DecaMeshc mesh when it "loads" it meshc file
    def __init__(self, v_path, material_properties=None):
        self.v_path = v_path
        self.models = None
        self.material_properties = material_properties

    def add_to_gltf(self, vfs: VfsDatabase, adf_db: AdfDatabase, db: Deca3dDatabase, gltf: pyg.GLTF2):
        if self.models is None:
            vfs.logger.log('Setup Modelc: {}'.format(self.v_path))

            nodes = node_of_type(vfs, self.v_path, TYPES_ADF_NODE)
            if len(nodes) == 0:
                # raise EDecaFileMissing('Not Mapped: {}'.format(self.v_path))
                vfs.logger.warning(f"{EDecaFileMissing('Not Mapped: {}'.format(self.v_path))}")
                return None

            node = nodes[0]
            model_adf = adf_db.read_node(vfs, node)
            model = AmfModel(model_adf, model_adf.table_instance_full_values[0])

            material_map = {}
            material: AmfMaterial
            for material in model.materials:
                if material.renderBlockId in RENDER_BLOCKS_DIF_NRM_MPM:
                    # add textures
                    textures = []
                    for texture_vpath in material.textures:
                        textures.append(db.gltf_add_texture(gltf, texture_vpath))

                    # [0] diffused color ,
                    # [1] normal bump map ,
                    # [2] MPM? ,
                    # [4] emissive , UseEmissive & EmissiveTextureHasColor
                    # [17] color_mask , UseColorMask

                    # add material
                    gltf_material = pyg.Material()
                    gltf_material.name = material.name.decode('utf-8')

                    # normal bump map
                    tid = textures[1]
                    if tid is not None:
                        gltf_material.normalTexture = pyg.NormalMaterialTexture(index=tid)

                    if material.attributes.get('UseEmissive', False):
                        gltf_material.emissiveFactor = [1.0, 1.0, 1.0]
                        tid = textures[4]
                        if tid is not None:
                            gltf_material.emissiveTexture = pyg.TextureInfo(index=tid)
                    else:
                        gltf_material.emissiveFactor = [0.0, 0.0, 0.0]

                    use_spec_gloss = False
                    if use_spec_gloss:
                        mat_sg = {}
                        tid = textures[0]
                        if tid is not None:
                            mat_sg['diffuseTexture'] = pyg.TextureInfo(index=tid)
                        tid = textures[2]
                        if tid is not None:
                            mat_sg['specularGlossinessTexture'] = pyg.TextureInfo(index=tid)

                        gltf_material.extensions['KHR_materials_pbrSpecularGlossiness'] = mat_sg
                    else:
                        mat_mr = pyg.PbrMetallicRoughness()

                        tid = textures[0]
                        if tid is not None:
                            mat_mr.baseColorTexture = pyg.TextureInfo(index=tid)

                        if len(textures) >= 3:
                            tid = textures[2]
                            if tid is not None:
                                mat_mr.metallicRoughnessTexture = pyg.TextureInfo(index=tid)

                        mat_mr.roughnessFactor = 1.0
                        mat_mr.metallicFactor = 1.0

                        gltf_material.pbrMetallicRoughness = mat_mr

                    # FOR TESTING, need to figure out how to use colormask with gltf
                    # if material.attributes['UseColorMask']:
                    #     tid = textures[17]
                    #     if tid is not None:
                    #         mat_sg['diffuseTexture'] = pyg.TextureInfo(index=tid)

                    material_finish(gltf_material, material)
                    material_idx = len(gltf.materials)
                    gltf.materials.append(gltf_material)
                    material_map[material.name] = material_idx
                elif b'GeneralMkIII' == material.renderBlockId:
                    # COTWed DECA, 2026-10-22 (audit 16-materiaux, 1 material in COTW) : [0] dif, [1] glo, [2] met,
                    # [3] nrm ; gloss and metal are separate maps, not a glTF metallic-roughness texture
                    textures = [db.gltf_add_texture(gltf, t) for t in material.textures]
                    gltf_material = pyg.Material()
                    gltf_material.name = material.name.decode('utf-8')
                    mat_mr = pyg.PbrMetallicRoughness()
                    if len(textures) > 0 and textures[0] is not None:
                        mat_mr.baseColorTexture = pyg.TextureInfo(index=textures[0])
                    mat_mr.roughnessFactor = 1.0
                    mat_mr.metallicFactor = 0.0
                    gltf_material.pbrMetallicRoughness = mat_mr
                    if len(textures) > 3 and textures[3] is not None:
                        gltf_material.normalTexture = pyg.NormalMaterialTexture(index=textures[3])
                    material_finish(gltf_material, material)
                    material_idx = len(gltf.materials)
                    gltf.materials.append(gltf_material)
                    material_map[material.name] = material_idx
                elif b'VegetationFoliage' == material.renderBlockId:
                    vfs.logger.log('Hacky version of RenderBlockId: {} Material Name: {}'.format(material.renderBlockId, material.name))
                    # add textures
                    textures = []
                    for texture_vpath in material.textures:
                        textures.append(db.gltf_add_texture(gltf, texture_vpath))

                    # [0] diffused color ,
                    # [1] normal bump map ,
                    # [2] ?
                    # [3] MPM? ,

                    # add material
                    gltf_material = pyg.Material()
                    gltf_material.name = material.name.decode('utf-8')

                    # normal bump map
                    tid = textures[1]
                    if tid is not None:
                        gltf_material.normalTexture = pyg.NormalMaterialTexture(index=tid)

                    gltf_material.emissiveFactor = [0.0, 0.0, 0.0]

                    use_spec_gloss = False
                    if use_spec_gloss:
                        mat_sg = {}
                        tid = textures[0]
                        if tid is not None:
                            mat_sg['diffuseTexture'] = pyg.TextureInfo(index=tid)
                        tid = textures[3]
                        if tid is not None:
                            mat_sg['specularGlossinessTexture'] = pyg.TextureInfo(index=tid)

                        gltf_material.extensions['KHR_materials_pbrSpecularGlossiness'] = mat_sg
                    else:
                        mat_mr = pyg.PbrMetallicRoughness()

                        tid = textures[0]
                        if tid is not None:
                            mat_mr.baseColorTexture = pyg.TextureInfo(index=tid)

                        tid = textures[3]
                        if tid is not None:
                            mat_mr.metallicRoughnessTexture = pyg.TextureInfo(index=tid)

                        mat_mr.roughnessFactor = 1.0
                        mat_mr.metallicFactor = 1.0

                        gltf_material.pbrMetallicRoughness = mat_mr

                    material_idx = len(gltf.materials)
                    gltf.materials.append(gltf_material)
                    material_map[material.name] = material_idx
                elif b'VegetationBark' == material.renderBlockId:
                    vfs.logger.log('Hacky version of RenderBlockId: {} Material Name: {}'.format(material.renderBlockId,
                                                                                                 material.name))
                    # add textures
                    textures = []
                    for texture_vpath in material.textures:
                        textures.append(db.gltf_add_texture(gltf, texture_vpath))

                    # [0] diffused color ,
                    # [1] normal bump map ,
                    # [2] MPM? ,
                    # ?

                    # add material
                    gltf_material = pyg.Material()
                    gltf_material.name = material.name.decode('utf-8')

                    # normal bump map
                    tid = textures[1]
                    if tid is not None:
                        gltf_material.normalTexture = pyg.NormalMaterialTexture(index=tid)

                    gltf_material.emissiveFactor = [0.0, 0.0, 0.0]

                    use_spec_gloss = False
                    if use_spec_gloss:
                        mat_sg = {}
                        tid = textures[0]
                        if tid is not None:
                            mat_sg['diffuseTexture'] = pyg.TextureInfo(index=tid)
                        tid = textures[2]
                        if tid is not None:
                            mat_sg['specularGlossinessTexture'] = pyg.TextureInfo(index=tid)

                        gltf_material.extensions['KHR_materials_pbrSpecularGlossiness'] = mat_sg
                    else:
                        mat_mr = pyg.PbrMetallicRoughness()

                        tid = textures[0]
                        if tid is not None:
                            mat_mr.baseColorTexture = pyg.TextureInfo(index=tid)

                        tid = textures[2]
                        if tid is not None:
                            mat_mr.metallicRoughnessTexture = pyg.TextureInfo(index=tid)

                        mat_mr.roughnessFactor = 1.0
                        mat_mr.metallicFactor = 1.0

                        gltf_material.pbrMetallicRoughness = mat_mr

                    material_idx = len(gltf.materials)
                    gltf.materials.append(gltf_material)
                    material_map[material.name] = material_idx
                else:
                    vfs.logger.log(f'Unhandled RenderBlockId: {material.renderBlockId} Material Name: {material.name}')

            meshes_all = db.gltf_add_meshc(gltf, model.mesh)
            meshes_new = {}
            for lod, meshes in meshes_all.items():
                ms = []
                for submeshes in meshes:
                    sms = []
                    for submesh in submeshes[0]:
                        mesh_new = db.gltf_finalize_mesh(submesh, material_map=material_map)
                        mesh_new_idx = len(gltf.meshes)
                        gltf.meshes.append(mesh_new)
                        sms.append(mesh_new_idx)
                    ms.append((sms, submeshes[1]))
                meshes_new[lod] = ms

            self.models = meshes_new

        return self.models


def quaternion_matrix(q) -> np.ndarray:
    """3x3 rotation of a unit quaternion (x, y, z, w), the order of Havok (hkQuaternion) and glTF."""
    x, y, z, w = (float(v) for v in q)
    n = x * x + y * y + z * z + w * w
    s = 2.0 / n if n > 0.0 else 0.0
    return np.array([
        [1.0 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
        [s * (x * y + z * w), 1.0 - s * (x * x + z * z), s * (y * z - x * w)],
        [s * (x * z - y * w), s * (y * z + x * w), 1.0 - s * (x * x + y * y)]])


def bone_transform(bone: dict):
    """(translation, rotation xyzw, scale) of a bone of ff_havok.decode_skeleton; identity without a reference pose."""
    return (list(bone.get('translation') or (0.0, 0.0, 0.0)), list(bone.get('rotation') or (0.0, 0.0, 0.0, 1.0)),
            list(bone.get('scale') or (1.0, 1.0, 1.0)))


def skeleton_matrices(bones):
    """Local and model-space (world) 4x4 matrices of the reference pose: world = world(parent) @ T @ R @ S.
    A parent index that is negative or not before the bone makes it a root (Havok orders parents first)."""
    local, world = [], []
    for i, bone in enumerate(bones):
        t, r, sc = bone_transform(bone)
        m = np.eye(4)
        m[0:3, 0:3] = quaternion_matrix(r) @ np.diag(sc)
        m[0:3, 3] = t
        local.append(m)
        p = bone.get('parent')
        world.append(world[p] @ m if p is not None and 0 <= p < i else m)
    return local, world


def skeleton_bone_node(skeleton, name):
    """glTF node of the bone of that name in Deca3dHkSkeleton.add_to_gltf's result, or None."""
    if isinstance(name, bytes):
        name = name.decode('utf-8', 'replace')
    for _idx, node in skeleton[2]:
        if node.name == name:
            return node
    return None


class Deca3dHkSkeleton:
    """Skin of a skeleton (.bsk, Havok packfile), read by deca.ff_havok (COTWed DECA, 2026-10-23). DECA ran the
    external bin2xml tool (cpp/bin2xml, HavokLib) and parsed its XML; the reference pose is the same: translation,
    rotation (x, y, z, w) and scale of each bone, relative to its parent."""
    def __init__(self, v_path, scene):
        self.v_path = v_path
        self.scene = scene
        self.skeleton = None

    def add_to_gltf(self, vfs: VfsDatabase, adf_db: AdfDatabase, db: Deca3dDatabase, gltf: pyg.GLTF2):
        if self.skeleton is None:
            from . import ff_havok
            vfs.logger.log('Setup Skeleton: {}'.format(self.v_path))

            # baked skeleton file name
            v_path = UniPath.splitext(self.v_path)[0] + b'.bsk'
            fn = v_path.decode('utf-8')
            if db.flat_file_layout:
                fn = fn.replace('/', '_')
            ppath_skel_uri = UniPath.join(db.resource_prefix_uri, fn)
            ppath_skel_raw = UniPath.join(db.resource_prefix_abs, fn)

            vnodes = node_of_type(vfs, v_path, TYPES_HAVOK_NODE)
            if len(vnodes) == 0:
                raise EDecaFileMissing('Not Mapped: {}'.format(v_path))
            with vfs.file_obj_from(vnodes[0]) as f:
                buffer = f.read()
            try:
                skels = ff_havok.skeletons(buffer)
            except ff_havok.EHavok as exc:
                raise EDecaErrorParse('Error parsing: {}: {}'.format(v_path, exc))
            if not skels or not skels[0]['bones']:
                raise EDecaErrorParse('Error parsing: {}: no hkaSkeleton with bones'.format(v_path))
            bones = skels[0]['bones']
            _local, world = skeleton_matrices(bones)

            bone_nodes = []
            for bi, bone in enumerate(bones):
                translate, rotate, scale = bone_transform(bone)
                pidx = bone.get('parent')
                bnode = pyg.Node()
                bnode_idx = len(gltf.nodes)
                gltf.nodes.append(bnode)

                # bones are not connected to the rest of the hierarchy: root bones are added to the scene
                if pidx is None or not 0 <= pidx < bi:
                    self.scene.nodes.append(bnode_idx)
                else:
                    bone_nodes[pidx][1].children.append(bnode_idx)
                bone_nodes.append([bnode_idx, bnode])
                bnode.name = bone['name']
                bnode.translation = [float(v) for v in translate]
                bnode.rotation = [float(v) for v in rotate]
                bnode.scale = [float(v) for v in scale]

            # inverse bind matrices, column major
            fn = ppath_skel_raw + '.ibm.dat'
            os.makedirs(UniPath.dirname(fn), exist_ok=True)
            with open(fn, 'wb') as f:
                for mat in world:
                    f.write(np.linalg.inv(mat).astype(dtype=np.float32).transpose().tobytes())

            # setup accessor
            buffer = pyg.Buffer()
            buffer.uri = ppath_skel_uri + '.ibm.dat'
            buffer.byteLength = 16 * 4 * len(world)
            buffer_idx = len(gltf.buffers)
            gltf.buffers.append(buffer)

            buffer_view = pyg.BufferView()
            buffer_view.buffer = buffer_idx
            buffer_view.byteOffset = 0
            buffer_view.byteLength = 16 * 4 * len(world)
            buffer_view_idx = len(gltf.bufferViews)
            gltf.bufferViews.append(buffer_view)

            accessor = pyg.Accessor()
            accessor.bufferView = buffer_view_idx
            accessor.byteOffset = 0
            accessor.count = len(world)
            accessor.type = "MAT4"
            accessor.componentType = pyg.FLOAT

            accessor_idx = len(gltf.accessors)
            gltf.accessors.append(accessor)

            # create skin
            skin = pyg.Skin()
            skin_joints = [bone_info[0] for bone_info in bone_nodes]
            skin.name = UniPath.basename(self.v_path.decode('utf-8')) + ".armature"
            skin.skeleton = skin_joints[0]
            skin.joints = skin_joints
            skin.inverseBindMatrices = accessor_idx
            skin_idx = len(gltf.skins)
            gltf.skins.append(skin)

            self.skeleton = (skin_idx, skin, bone_nodes)

        return self.skeleton


class DecaGltfScene:
    def __init__(self, deca_gltf, name=None):
        if isinstance(name, bytes):
            name = name.decode('utf-8')
        self.deca_gltf = deca_gltf
        self.index = None
        self.scene = pyg.Scene(name=name)

    def __enter__(self):
        self.deca_gltf.gltf_push(self)
        return self.scene

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.deca_gltf.gltf_pop(self)


class DecaGltfNode:
    def __init__(self, deca_gltf, name=None, matrix=None):
        if isinstance(name, bytes):
            name = name.decode('utf-8')
        self.deca_gltf = deca_gltf
        self.index = None
        self.node = pyg.Node(name=name, matrix=matrix)

    def __enter__(self):
        self.deca_gltf.gltf_push(self)
        return self.node

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.deca_gltf.gltf_pop(self)


class DecaGltf:
    def __init__(
            self, vfs: VfsDatabase, export_path, filename, lod=0,
            save_to_one_dir=False,
            flat_file_layout=False,
            include_skeleton=False,
            texture_format=None):
        self.vfs = vfs
        self.filename = filename
        self.lod = lod
        self.save_to_one_dir = save_to_one_dir
        self.flat_file_layout = flat_file_layout
        self.include_skeleton = include_skeleton
        if texture_format is None:
            self.texture_format = 'dds'
        else:
            self.texture_format = texture_format
        self.gltf = pyg.GLTF2()
        self.gltf.asset.version = "2.0"
        self.gltf.asset.generator = "DECA extractor"
        self.gltf.extensionsUsed.append('KHR_materials_pbrSpecularGlossiness')
        self.d_stack = []
        self.d_scene = DecaGltfScene(self, name='Scene0')

        if self.save_to_one_dir:
            self.export_path = UniPath.join(export_path, filename + '.dir', 'model-lod_{}.gltf'.format(self.lod))
            self.resource_prefix_abs = UniPath.join(export_path, filename + '.dir')
            self.resource_prefix_uri = ''
        else:
            self.export_path = UniPath.join(export_path, filename + '-lod_{}.gltf'.format(self.lod))
            self.resource_prefix_abs = export_path
            self.resource_prefix_uri = ''
            dirs = UniPath.dirname(filename)
            while len(dirs) > 0:
                self.resource_prefix_uri += '../'
                dirs = UniPath.dirname(dirs)
        os.makedirs(UniPath.dirname(self.export_path), exist_ok=True)

        self.db = Deca3dDatabase(
            self.vfs,
            self.resource_prefix_abs,
            self.resource_prefix_uri,
            self.flat_file_layout,
            self.texture_format)

    def gltf_save(self):
        assert self.gltf is not None
        assert len(self.d_stack) == 0
        self.gltf.save_json(self.export_path)

    def scene(self):
        return self.d_scene

    def gltf_push(self, item):
        if isinstance(item, DecaGltfScene):
            assert len(self.d_stack) == 0
            if item.index is None:
                item.index = len(self.gltf.scenes)
                self.gltf.scenes.append(item.scene)
                if item.index == 0:
                    self.gltf.scene = item.index

        elif isinstance(item, DecaGltfNode):
            assert len(self.d_stack) > 0  # at least scene has to be on stack
            assert item not in self.d_stack  # cannot already be on stack

            parent = self.d_stack[-1]
            assert isinstance(parent, DecaGltfNode) or isinstance(parent, DecaGltfScene)

            if item.index is None:
                item.index = len(self.gltf.nodes)
                self.gltf.nodes.append(item.node)

                if isinstance(parent, DecaGltfScene):
                    parent.scene.nodes.append(item.index)
                elif isinstance(parent, DecaGltfNode):
                    parent.node.children.append(item.index)

            if isinstance(parent, DecaGltfScene):
                assert item.index in parent.scene.nodes  # assert that is childen of parent
            elif isinstance(parent, DecaGltfNode):
                assert item.index in parent.node.children  # assert that is childen of parent

        self.d_stack.append(item)

    def gltf_pop(self, item):
        assert len(self.d_stack) > 0
        assert self.d_stack[-1] == item
        self.d_stack.pop(-1)

    def export_modelc(self, v_path, transform: Optional[Deca3dMatrix], material_properties=None, skeleton_raw_path=None,
                      attach_bone: Optional[bytes] = None, attach_matrix: Optional[Deca3dMatrix] = None):
        """attach_bone: the model is a part hung on that bone of the skeleton (weapon attachment, see
        export_import_rtpc.h_prop_attach_bone): its mesh is given in the bone's frame, so it is exported rigid (no
        skin) as a child of the bone node, under attach_matrix (the attachment's own 'world', relative to the bone).
        Without the skeleton or the bone, the model is exported as before."""
        if transform is None:
            transform = Deca3dMatrix()

        self.vfs.logger.log('export_modelc: Started')
        # setup skeleton
        skeleton = None
        if self.include_skeleton and skeleton_raw_path is not None:
            skeleton = self.db.gltf_add_hk_skeleton(self.gltf, skeleton_raw_path, self.d_scene.scene)

        # setup materials
        meshes_all = self.db.gltf_add_modelc(
            self.gltf, v_path, material_properties=material_properties)

        bone = None
        if meshes_all is not None and attach_bone is not None:
            bone = skeleton_bone_node(skeleton, attach_bone) if skeleton is not None else None
            if bone is None:
                self.vfs.logger.log('export_modelc: attachment bone {} not found, {} exported unattached'.format(
                    attach_bone, v_path))

        if meshes_all is not None and bone is not None:
            holder = pyg.Node(name=UniPath.basename(v_path).decode('utf-8') if isinstance(v_path, bytes)
                              else UniPath.basename(v_path),
                              matrix=(attach_matrix or Deca3dMatrix()).col_major_list())
            holder_idx = len(self.gltf.nodes)
            self.gltf.nodes.append(holder)
            bone.children.append(holder_idx)
            for mesh_info in meshes_all[self.lod]:
                for submesh in mesh_info[0]:
                    holder.children.append(len(self.gltf.nodes))
                    self.gltf.nodes.append(pyg.Node(mesh=submesh))
        elif meshes_all is not None:
            with DecaGltfNode(self, name=UniPath.basename(v_path), matrix=transform.col_major_list()):
                for mesh_info in meshes_all[self.lod]:
                    submeshes = mesh_info[0]

                    skin_idx = None
                    if mesh_info[1] and skeleton is not None:
                        skin_idx = skeleton[0]

                    for submesh in submeshes:
                        with DecaGltfNode(self) as mesh_node:
                            mesh_node.mesh = submesh
                            mesh_node.skin = skin_idx

        self.vfs.logger.log('export_modelc: Complete')

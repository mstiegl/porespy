import scipy as sp
import numpy as np
import openpnm as op
from porespy.tools import make_contiguous
from porespy.filters import snow_partitioning
from collections import namedtuple
from skimage.segmentation import find_boundaries
from skimage.morphology import ball, cube, disk
from scipy.ndimage import distance_transform_edt
from tqdm import tqdm


def map_to_regions(regions, values):
    r"""
    Maps pore values from a network onto the image from which it was extracted

    This function assumes that the pore numbering in the network has remained
    unchanged from the region labels in the partitioned image.

    Parameters
    ----------
    regions : ND-array
        An image of the pore space partitioned into regions and labeled

    values : array_like
        An array containing the numerical values to insert into each region.
        The value at location *n* will be inserted into the image where
        ``regions`` is *n+1*.  This mis-match is caused by the fact that 0's
        in the ``regions`` image is assumed to be the backgroung phase, while
        pore index 0 is valid.

    Notes
    -----
    This function assumes that the array of pore values are indexed starting
    at location 0, while in the region image 0's indicate background phase and
    the region indexing starts at 1.  That is, region 1 corresponds to pore 0.

    """
    values = sp.array(values).flatten()
    if sp.size(values) != regions.max() + 1:
        raise Exception('Number of values does not match number of regions')
    im = sp.zeros_like(regions)
    im = values[regions]
    return im


def add_boundary_regions(regions=None, faces=['front', 'back', 'left',
                                              'right', 'top', 'bottom']):
    # -------------------------------------------------------------------------
    # Edge pad segmentation and distance transform
    if faces is not None:
        regions = sp.pad(regions, 1, 'edge')
        # ---------------------------------------------------------------------
        if regions.ndim == 3:
            # Remove boundary nodes interconnection
            regions[:, :, 0] = regions[:, :, 0] + regions.max()
            regions[:, :, -1] = regions[:, :, -1] + regions.max()
            regions[0, :, :] = regions[0, :, :] + regions.max()
            regions[-1, :, :] = regions[-1, :, :] + regions.max()
            regions[:, 0, :] = regions[:, 0, :] + regions.max()
            regions[:, -1, :] = regions[:, -1, :] + regions.max()
            regions[:, :, 0] = (~find_boundaries(regions[:, :, 0],
                                                 mode='outer'))*regions[:, :, 0]
            regions[:, :, -1] = (~find_boundaries(regions[:, :, -1],
                                                  mode='outer'))*regions[:, :, -1]
            regions[0, :, :] = (~find_boundaries(regions[0, :, :],
                                                 mode='outer'))*regions[0, :, :]
            regions[-1, :, :] = (~find_boundaries(regions[-1, :, :],
                                                  mode='outer'))*regions[-1, :, :]
            regions[:, 0, :] = (~find_boundaries(regions[:, 0, :],
                                                 mode='outer'))*regions[:, 0, :]
            regions[:, -1, :] = (~find_boundaries(regions[:, -1, :],
                                                  mode='outer'))*regions[:, -1, :]
            # -----------------------------------------------------------------
            regions = sp.pad(regions, 2, 'edge')

            # Remove unselected faces
            if 'front' not in faces:
                regions = regions[:, 3:, :]  # y
            if 'back' not in faces:
                regions = regions[:, :-3, :]
            if 'left' not in faces:
                regions = regions[3:, :, :]  # x
            if 'right' not in faces:
                regions = regions[:-3, :, :]
            if 'bottom' not in faces:
                regions = regions[:, :, 3:]  # z
            if 'top' not in faces:
                regions = regions[:, :, :-3]

        elif regions.ndim == 2:
            # Remove boundary nodes interconnection
            regions[0, :] = regions[0, :] + regions.max()
            regions[-1, :] = regions[-1, :] + regions.max()
            regions[:, 0] = regions[:, 0] + regions.max()
            regions[:, -1] = regions[:, -1] + regions.max()
            regions[0, :] = (~find_boundaries(regions[0, :],
                                              mode='outer'))*regions[0, :]
            regions[-1, :] = (~find_boundaries(regions[-1, :],
                                               mode='outer'))*regions[-1, :]
            regions[:, 0] = (~find_boundaries(regions[:, 0],
                                              mode='outer'))*regions[:, 0]
            regions[:, -1] = (~find_boundaries(regions[:, -1],
                                               mode='outer'))*regions[:, -1]
            # -----------------------------------------------------------------
            regions = sp.pad(regions, 2, 'edge')

            # Remove unselected faces
            if 'left' not in faces:
                regions = regions[3:, :]  # x
            if 'right' not in faces:
                regions = regions[:-3, :]
            if 'front' not in faces and 'bottom' not in faces:
                regions = regions[:, 3:]  # y
            if 'back' not in faces and 'top' not in faces:
                regions = regions[:, :-3]
        else:
            print('add_boundary_regions works only on 2D and 3D images')
        # ---------------------------------------------------------------------
        # Make labels contiguous
        regions = make_contiguous(regions)
    else:
        regions = regions

    return regions


def overlay(im1, im2, c):
    r"""
    Overlays im2 onto im1, given voxel coords of center of im2 in im1.

    Parameters
    ----------
    im1 : 3D numpy array
        Original voxelated image

    im2 : 3D numpy array
        Template voxelated image

    r : int
        Radius of the cylinder

    Returns
    -------
    im1 : 3D numpy array
        Original voxelated image overlayed with the template

    """
    shape = im2.shape

    for ni in shape:
        if ni % 2 == 0:
            raise Exception("Structuring element must be odd-voxeled...")

    nx, ny, nz = [(ni - 1) // 2 for ni in shape]
    cx, cy, cz = c

    im1[cx-nx:cx+nx+1, cy-ny:cy+ny+1, cz-nz:cz+nz+1] += im2

    return im1


def add_cylinder_to(im, xyz0, xyz1, r):
    r"""
    Overlays a cylinder of given radius onto a given 3d image.

    Parameters
    ----------
    im : 3D numpy array
        Original voxelated image

    xyz0, xyz1 : 3 by 1 numpy array-like
        Voxel coordinates of the two end points of the cylinder

    r : int
        Radius of the cylinder

    Returns
    -------
    im : 3D numpy array
        Original voxelated image overlayed with the cylinder

    """
    # Converting coordinates to numpy array
    xyz0, xyz1 = [np.array(xyz).astype(int) for xyz in (xyz0, xyz1)]
    r = int(r)
    L = np.abs(xyz0 - xyz1).max() + 1
    xyz_line = [np.linspace(xyz0[i], xyz1[i], L).astype(int) for i in range(3)]

    xyz_min = np.min(xyz_line, axis=1) - r
    xyz_max = np.max(xyz_line, axis=1) + r
    shape_template = xyz_max - xyz_min + 1
    template = np.zeros(shape=shape_template)

    # Shortcut for orthogonal cylinders
    if (xyz0 == xyz1).sum() == 2:
        unique_dim = [xyz0[i] != xyz1[i] for i in range(3)].index(True)
        shape_template[unique_dim] = 1
        template_2D = disk(radius=r).reshape(shape_template)
        template = np.repeat(template_2D, repeats=L, axis=unique_dim)
        xyz_min[unique_dim] += r
        xyz_max[unique_dim] += -r
    else:
        xyz_line_in_template_coords = [xyz_line[i] - xyz_min[i] for i in range(3)]
        template[tuple(xyz_line_in_template_coords)] = 1
        template = distance_transform_edt(template == 0) <= r

    im[xyz_min[0]:xyz_max[0]+1,
       xyz_min[1]:xyz_max[1]+1,
       xyz_min[2]:xyz_max[2]+1] += template

    return im


def _generate_voxel_image(network, pore_shape, throat_shape, max_dim=200,
                          verbose=1):
    r"""
    Generates a 3d numpy array from a network model.

    Parameters
    ----------
    network : OpenPNM GenericNetwork
        Network from which voxel image is to be generated

    pore_shape : str
        Shape of pores in the network, valid choices are "sphere", "cube"

    throat_shape : str
        Shape of throats in the network, valid choices are "cylinder", "cuboid"

    max_dim : int
        Number of voxels in the largest dimension of the network

    Returns
    -------
    im : 3D numpy array
        Voxelated image corresponding to the given pore network model

    Notes
    -----
    (1) The generated voxelated image is labeled with 0s, 1s and 2s signifying
    solid phase, pores, and throats respectively.

    """
    xyz = network["pore.coords"]
    cn = network["throat.conns"]

    # Distance bounding box from the network by a fixed amount
    delta = network["pore.diameter"].mean() / 2
    if isinstance(network, op.network.Cubic):
        delta = network._spacing.mean() / 2

    # Shift everything to avoid out-of-bounds
    extra_clearance = int(max_dim * 0.05)

    # Transform points to satisfy origin at (0, 0, 0)
    xyz0 = xyz.min(axis=0) - delta
    xyz += -xyz0
    res = (xyz.ptp(axis=0).max() + 2*delta) / max_dim
    shape = np.rint((xyz.max(axis=0) + delta) / res).astype(int) + 2*extra_clearance

    # Transforming from real coords to matrix coords
    xyz = np.rint(xyz / res).astype(int) + extra_clearance
    pore_radi = np.rint(network["pore.diameter"] * 0.5 / res).astype(int)
    throat_radi = np.rint(network["throat.diameter"] * 0.5 / res).astype(int)

    im_pores = np.zeros(shape, dtype=np.uint8)
    im_throats = np.zeros_like(im_pores)

    if pore_shape is "cube":
        pore_elem = cube
        rp = pore_radi * 2 + 1  # +1 since num_voxel must be odd
        rp_max = int(2 * round(delta / res)) + 1
    if pore_shape is "sphere":
        pore_elem = ball
        rp = pore_radi
        rp_max = int(round(delta / res))
    if throat_shape is "cuboid":
        raise Exception("Not yet implemented, try 'cylinder'.")

    # Generating voxels for pores
    for i, pore in enumerate(tqdm(network.pores(), disable=not verbose,
                                  desc="Generating pores  ")):
        elem = pore_elem(rp[i])
        try:
            im_pores = overlay(im1=im_pores, im2=elem, c=xyz[i])
        except ValueError:
            elem = pore_elem(rp_max)
            im_pores = overlay(im1=im_pores, im2=elem, c=xyz[i])
    # Get rid of pore overlaps
    im_pores[im_pores > 0] = 1

    # Generating voxels for throats
    for i, throat in enumerate(tqdm(network.throats(), disable=not verbose,
                                    desc="Generating throats")):
        try:
            im_throats = add_cylinder_to(im_throats, r=throat_radi[i],
                                         xyz0=xyz[cn[i, 0]], xyz1=xyz[cn[i, 1]])
        except ValueError:
            im_throats = add_cylinder_to(im_throats, r=rp_max,
                                         xyz0=xyz[cn[i, 0]], xyz1=xyz[cn[i, 1]])
    # Get rid of throat overlaps
    im_throats[im_throats > 0] = 1

    # Subtract pore-throat overlap from throats
    im_throats = (im_throats.astype(bool) * ~im_pores.astype(bool)).astype(sp.uint8)
    im = im_pores * 1 + im_throats * 2

    return im[extra_clearance:-extra_clearance,
              extra_clearance:-extra_clearance,
              extra_clearance:-extra_clearance]

    return im


def generate_voxel_image(network, pore_shape="sphere", throat_shape="cylinder",
                         max_dim=None, verbose=1, rtol=0.1):
    r"""
    Generates voxel image from an OpenPNM network object.

    Parameters
    ----------
    network : OpenPNM GenericNetwork
        Network from which voxel image is to be generated

    pore_shape : str
        Shape of pores in the network, valid choices are "sphere", "cube"

    throat_shape : str
        Shape of throats in the network, valid choices are "cylinder", "cuboid"

    max_dim : int
        Number of voxels in the largest dimension of the network

    rtol : float
        Stopping criteria for finding the smallest voxel image such that further
        increasing the number of voxels in each dimension by 25% would improve
        the predicted porosity of the image by less that ``rtol``

    Returns
    -------
    im : 3D numpy array
        Voxelated image corresponding to the given pore network model

    Notes
    -----
    (1) The generated voxelated image is labeled with 0s, 1s and 2s signifying
    solid phase, pores, and throats respectively.

    (2) If max_dim is not provided, the method calculates it such that the
    further increasing it doesn't change porosity by much.

    """
    print("\n" + "-" * 44, flush=True)
    print("| Generating voxel image from pore network |", flush=True)
    print("-" * 44, flush=True)

    # If max_dim is provided, generate voxel image using max_dim
    if max_dim is not None:
        return _generate_voxel_image(network, pore_shape, throat_shape,
                                     max_dim=max_dim, verbose=verbose)
    else:
        max_dim = 200

    # If max_dim is not provided, find best max_dim that predicts porosity
    eps_old = 200
    err = 100  # percent

    while err > rtol:
        im = _generate_voxel_image(network, pore_shape, throat_shape,
                                   max_dim=max_dim, verbose=verbose)
        eps = im.astype(bool).sum() / sp.prod(im.shape)

        err = abs(1 - eps/eps_old)
        eps_old = eps
        max_dim = int(max_dim * 1.25)

    if verbose:
        print(f"\nConverged at max_dim = {max_dim} voxels.\n")

    return im


def connect_network_phases(net, snow_partitioning_n, voxel_size=1,
                           alias=None,
                           marching_cubes_area=False):
    r"""
    This function connects networks of two or more than two phases together by
    interconnecting negibouring nodes inside different phases. The resulting
    network can be used for the study of transport and kinetics at interphase
    of two phases.

    Parameters
    ----------
    network : 2D or 3D network
        A dictoionary containing structural information of two or more than two
        phases networks. The dictonary format must be same as porespy
        region_to_network function.

    snow_partitioning_n : tuple
        The output generated by snow_partitioning_n function. The tuple should
        have phases_max_labels and original image of material.

    voxel_size : scalar
        The resolution of the image, expressed as the length of one side of a
        voxel, so the volume of a voxel would be **voxel_size**-cubed.  The
        default is 1, which is useful when overlaying the PNM on the original
        image since the scale of the image is alway 1 unit lenth per voxel.

    alias : dict (Optional)
        A dictionary that assigns unique image label to specific phase.
        For example {1: 'Solid'} will show all structural properties associated
        with label 1 as Solid phase properties.
        If ``None`` then default labelling will be used i.e {1: 'Phase1',..}.

    marching_cubes_area : bool
        If ``True`` then the surface area and interfacial area between regions
        will be using the marching cube algorithm. This is a more accurate
        representation of area in extracted network, but is quite slow, so
        it is ``False`` by default.  The default method simply counts voxels
        so does not correctly account for the voxelated nature of the images.

    Returns
    -------
    A dictionary containing network information of individual and connected
    networks. The dictionary names use the OpenPNM convention so it may be
    converted directly to an OpenPNM network object using the ``update`` command.

    """
    # -------------------------------------------------------------------------
    # Get alias if provided by user
    im = snow_partitioning_n.im
    al = assign_alias(im, alias=alias)
    # -------------------------------------------------------------------------
    # Find interconnection and interfacial area between ith and jth phases
    conns1 = net['throat.conns'][:, 0]
    conns2 = net['throat.conns'][:, 1]
    label = net['pore.label'] - 1

    num = snow_partitioning_n.phase_max_label
    num = [0, *num]
    phases_num = sp.unique(im * 1)
    phases_num = sp.trim_zeros(phases_num)
    for i in phases_num:
        loc1 = sp.logical_and(conns1 >= num[i - 1], conns1 < num[i])
        loc2 = sp.logical_and(conns2 >= num[i - 1], conns2 < num[i])
        loc3 = sp.logical_and(label >= num[i - 1], label < num[i])
        net['throat.{}'.format(al[i])] = loc1 * loc2
        net['pore.{}'.format(al[i])] = loc3
        if i == phases_num[-1]:
            loc4 = sp.logical_and(conns1 < num[-1], conns2 >= num[-1])
            loc5 = label >= num[-1]
            net['throat.boundary'] = loc4
            net['pore.boundary'] = loc5
        for j in phases_num:
            if j > i:
                pi_pj_sa = sp.zeros_like(label)
                loc6 = sp.logical_and(conns2 >= num[j - 1], conns2 < num[j])
                pi_pj_conns = loc1 * loc6
                net['throat.{}_{}'.format(al[i], al[j])] = pi_pj_conns
                if any(pi_pj_conns):
                    # ---------------------------------------------------------
                    # Calculates phase[i] interfacial area that connects with
                    # phase[j] and vice versa
                    p_conns = net['throat.conns'][:, 0][pi_pj_conns]
                    s_conns = net['throat.conns'][:, 1][pi_pj_conns]
                    ps = net['throat.area'][pi_pj_conns]
                    p_sa = sp.bincount(p_conns, ps)
                    # trim zeros at head/tail position to avoid extra bins
                    p_sa = sp.trim_zeros(p_sa)
                    i_index = sp.arange(min(p_conns), max(p_conns) + 1)
                    j_index = sp.arange(min(s_conns), max(s_conns) + 1)
                    s_pa = sp.bincount(s_conns, ps)
                    s_pa = sp.trim_zeros(s_pa)
                    pi_pj_sa[i_index] = p_sa
                    pi_pj_sa[j_index] = s_pa
                    # ---------------------------------------------------------
                    # Calculates interfacial area using marching cube method
                    if marching_cubes_area:
                        ps_c = net['throat.area'][pi_pj_conns]
                        p_sa_c = sp.bincount(p_conns, ps_c)
                        p_sa_c = sp.trim_zeros(p_sa_c)
                        s_pa_c = sp.bincount(s_conns, ps_c)
                        s_pa_c = sp.trim_zeros(s_pa_c)
                        pi_pj_sa[i_index] = p_sa_c
                        pi_pj_sa[j_index] = s_pa_c
                    net['pore.{}_{}_area'.format(al[i], al[j])] = (
                            pi_pj_sa *
                            voxel_size ** 2)
    return net


def label_boundary_cells(network=None, boundary_faces=None):
    r"""
    Takes 2D or 3D network and assign labels to boundary pores

    Parameters
    ----------
    network : 2D or 3D network
        Network should contains nodes coordinates for phase under consideration

    boundary_faces : list of strings
        The user can choose ‘left’, ‘right’, ‘top’, ‘bottom’, ‘front’ and
        ‘back’ face labels to assign boundary nodes. If no label is
        assigned then all six faces will be selected as boundary nodes
        automatically which can be trimmed later on based on user requirements.

    Returns
    -------
    A dictionary containing boundary nodes labels for example
    network['pore.left'], network['pore.right'], network['pore.top'],
    network['pore.bottom'] etc.
    The dictionary names use the OpenPNM convention so it may be converted
    directly to an OpenPNM network object using the ``update`` command.

    """
    f = boundary_faces
    if f is not None:
        coords = network['pore.coords']
        condition = coords[~network['pore.boundary']]
        dic = {'left': 0, 'right': 0, 'front': 1, 'back': 1,
               'top': 2, 'bottom': 2}
        if all(coords[:, 2] == 0):
            dic['top'] = 1
            dic['bottom'] = 1
        for i in f:
            if i in ['left', 'front', 'bottom']:
                network['pore.{}'.format(i)] = (coords[:, dic[i]] <
                                                min(condition[:, dic[i]]))
            elif i in ['right', 'back', 'top']:
                network['pore.{}'.format(i)] = (coords[:, dic[i]] >
                                                max(condition[:, dic[i]]))

    return network


def assign_alias(im, alias=None):
    r"""
    The function assigns unique label to specific phase in original image. This
    alias can be used to distinguish two phase interconnection and properties
    easily when we have two or more than two phase network during network
    extraction process.

    Parameters
    ----------
    im : ND-array
        Image of porous material where each phase is represented by unique
        integer. Phase integer should start from 1. Boolean image will extract
        only one network labeled with True's only.

    alias : dict (Optional)
        A dictionary that assigns unique image label to specific phase.
        For example {1: 'Solid'} will show all structural properties associated
        with label 1 as Solid phase properties.
        If ``None`` then default labelling will be used i.e {1: 'Phase1',..}.

    Returns
    -------
    A dictionary which assigns unique name to all unique labels of phases in
    original image. If no alias is provided then default labelling is used
    i.e {1: 'Phase1',..}
    """
    # -------------------------------------------------------------------------
    # Get alias if provided by user
    phases_num = sp.unique(im * 1)
    phases_num = sp.trim_zeros(phases_num)
    al = {}
    for values in phases_num:
        al[values] = 'phase{}'.format(values)
    if alias is not None:
        alias_sort = dict(sorted(alias.items()))
        phase_labels = sp.array([*alias_sort])
        al = alias
        if set(phase_labels) != set(phases_num):
            raise Exception('Alias labels does not match with image labels '
                            'please provide correct image labels')
    return al
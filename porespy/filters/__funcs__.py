from collections import namedtuple
import scipy as sp
import numpy as np
import scipy.ndimage as spim
import scipy.spatial as sptl
from scipy.signal import fftconvolve
from tqdm import tqdm
from numba import jit
from skimage.segmentation import clear_border
from skimage.morphology import ball, disk, square, cube, diamond, octahedron
from skimage.morphology import reconstruction, watershed
from porespy.tools import randomize_colors, fftmorphology
from porespy.tools import get_border, extend_slice
from porespy.tools import ps_disk, ps_ball


def distance_transform_lin(im, axis=0, mode='both'):
    r"""
    Replaces each void voxel with the linear distance to the nearest solid
    voxel along the specified axis.

    Parameters
    ----------
    im : ND-array
        The image of the porous material with ``True`` values indicating the
        void phase (or phase of interest)

    axis : int
        The direction along which the distance should be measured, the default
        is 0 (i.e. along the x-direction)

    mode : string
        Controls how the distance is measured.  Options are:

        'forward' - Distances are measured in the increasing direction along
        the specified axis

        'reverse' - Distances are measured in the reverse direction.
        *'backward'* is also accepted.

        'both' - Distances are calculated in both directions (by recursively
        calling itself), then reporting the minimum value of the two results.

    Returns
    -------
    image : ND-array
        A copy of ``im`` with each foreground voxel containing the distance to
        the nearest background along the specified axis.
    """
    if mode in ['backward', 'reverse']:
        im = sp.flip(im, axis)
        im = distance_transform_lin(im=im, axis=axis, mode='forward')
        im = sp.flip(im, axis)
        return im
    elif mode in ['both']:
        im_f = distance_transform_lin(im=im, axis=axis, mode='forward')
        im_b = distance_transform_lin(im=im, axis=axis, mode='backward')
        return sp.minimum(im_f, im_b)
    else:
        b = sp.cumsum(im > 0, axis=axis)
        c = sp.diff(b*(im == 0), axis=axis)
        d = sp.minimum.accumulate(c, axis=axis)
        if im.ndim == 1:
            e = sp.pad(d, pad_width=[1, 0], mode='constant', constant_values=0)
        elif im.ndim == 2:
            ax = [[[1, 0], [0, 0]], [[0, 0], [1, 0]]]
            e = sp.pad(d, pad_width=ax[axis], mode='constant', constant_values=0)
        elif im.ndim == 3:
            ax = [[[1, 0], [0, 0], [0, 0]],
                  [[0, 0], [1, 0], [0, 0]],
                  [[0, 0], [0, 0], [1, 0]]]
            e = sp.pad(d, pad_width=ax[axis], mode='constant', constant_values=0)
        f = im*(b + e)
        return f


def snow_partitioning(im, dt=None, r_max=4, sigma=0.4, return_all=False,
                      mask=True, randomize=True):
    r"""
    Partitions the void space into pore regions using a marker-based watershed
    algorithm, with specially filtered peaks as markers.

    The SNOW network extraction algorithm (Sub-Network of an Over-segmented
    Watershed) was designed to handle to perculiarities of high porosity
    materials, but it applies well to other materials as well.

    Parameters
    ----------
    im : array_like
        A boolean image of the domain, with ``True`` indicating the pore space
        and ``False`` elsewhere.
    dt : array_like, optional
        The distance transform of the pore space.  This is done automatically
        if not provided, but if the distance transform has already been
        computed then supplying it can save some time.
    r_max : int
        The radius of the spherical structuring element to use in the Maximum
        filter stage that is used to find peaks.  The default is 4
    sigma : float
        The standard deviation of the Gaussian filter used in step 1.  The
        default is 0.4.  If 0 is given then the filter is not applied, which is
        useful if a distance transform is supplied as the ``im`` argument that
        has already been processed.
    return_all : boolean
        If set to ``True`` a named tuple is returned containing the original
        image, the distance transform, the filtered peaks, and the final
        pore regions.  The default is ``False``
    mask : boolean
        Apply a mask to the regions where the solid phase is.  Default is
        ``True``
    randomize : boolean
        If ``True`` (default), then the region colors will be randomized before
        returning.  This is helpful for visualizing otherwise neighboring
        regions have simlar coloring are are hard to distinguish.

    Returns
    -------
    image : ND-array
        An image the same shape as ``im`` with the void space partitioned into
        pores using a marker based watershed with the peaks found by the
        SNOW algorithm [1].

    Notes
    -----
    If ``return_all`` is ``True`` then a **named tuple** is returned containing
    all of the images used during the process.  They can be access as
    attriutes with the following names:

        * ``im``: The binary image of the void space
        * ``dt``: The distance transform of the image
        * ``peaks``: The peaks of the distance transform after applying the
        steps of the SNOW algorithm
        * ``regions``: The void space partitioned into pores using a marker
        based watershed with the peaks found by the SNOW algorithm

    References
    ----------
    [1] Gostick, J. "A versatile and efficient network extraction algorithm
    using marker-based watershed segmenation".  Physical Review E. (2017)

    """
    tup = namedtuple('results', field_names=['im', 'dt', 'peaks', 'regions'])
    print('_'*60)
    print("Beginning SNOW Algorithm")
    im_shape = sp.array(im.shape)
    if im.dtype is not bool:
        print('Converting supplied image (im) to boolean')
        im = im > 0
    if dt is None:
        print('Peforming Distance Transform')
        if sp.any(im_shape == 1):
            ax = sp.where(im_shape == 1)[0][0]
            dt = spim.distance_transform_edt(input=im.squeeze())
            dt = sp.expand_dims(dt, ax)
        else:
            dt = spim.distance_transform_edt(input=im)

    tup.im = im
    tup.dt = dt

    if sigma > 0:
        print('Applying Gaussian blur with sigma =', str(sigma))
        dt = spim.gaussian_filter(input=dt, sigma=sigma)

    peaks = find_peaks(dt=dt, r_max=r_max)
    print('Initial number of peaks: ', spim.label(peaks)[1])
    peaks = trim_saddle_points(peaks=peaks, dt=dt, max_iters=500)
    print('Peaks after trimming saddle points: ', spim.label(peaks)[1])
    peaks = trim_nearby_peaks(peaks=peaks, dt=dt)
    peaks, N = spim.label(peaks)
    print('Peaks after trimming nearby peaks: ', N)
    tup.peaks = peaks
    if mask:
        mask_solid = im > 0
    else:
        mask_solid = None
    regions = watershed(image=-dt, markers=peaks, mask=mask_solid)
    if randomize:
        regions = randomize_colors(regions)
    if return_all:
        tup.regions = regions
        return tup
    else:
        return regions


def find_peaks(dt, r_max=4, footprint=None):
    r"""
    Returns all local maxima in the distance transform

    Parameters
    ----------
    dt : ND-array
        The distance transform of the pore space.  This may be calculated and
        filtered using any means desired.

    r_max : scalar
        The size of the structuring element used in the maximum filter.  This
        controls the localness of any maxima. The default is 4 voxels.

    footprint : ND-array
        Specifies the shape of the structuring element used to define the
        neighborhood when looking for peaks.  If none is specified then a
        spherical shape is used (or circular in 2D).

    Returns
    -------
    image : ND-array
        An array of booleans with ``True`` values at the location of any
        local maxima.

    Notes
    -----
    It is also possible ot the ``peak_local_max`` function from the
    ``skimage.feature`` module as follows:

    ``peaks = peak_local_max(image=dt, min_distance=r, exclude_border=0,
    indices=False)``

    This automatically uses a square structuring element which is significantly
    faster than using a circular or spherical element.
    """
    im = dt > 0
    if footprint is None:
        if im.ndim == 2:
            footprint = disk
        elif im.ndim == 3:
            footprint = ball
        else:
            raise Exception("only 2-d and 3-d images are supported")
    mx = spim.maximum_filter(dt + 2*(~im), footprint=footprint(r_max))
    peaks = (dt == mx)*im
    return peaks


def reduce_peaks(peaks):
    r"""
    Any peaks that are broad or elongated are replaced with a single voxel
    that is located at the center of mass of the original voxels.

    Parameters
    ----------
    peaks : ND-image
        An image containing True values indicating peaks in the distance
        transform

    Returns
    -------
    image : ND-array
        An array with the same number of isolated peaks as the original image,
        but fewer total voxels.

    Notes
    -----
    The center of mass of a group of voxels is used as the new single voxel, so
    if the group has an odd shape (like a horse shoe), the new voxel may *not*
    lie on top of the original set.
    """
    if peaks.ndim == 2:
        strel = square
    else:
        strel = cube
    markers, N = spim.label(input=peaks, structure=strel(3))
    inds = spim.measurements.center_of_mass(input=peaks,
                                            labels=markers,
                                            index=sp.arange(1, N+1))
    inds = sp.floor(inds).astype(int)
    # Centroid may not be on old pixel, so create a new peaks image
    peaks_new = sp.zeros_like(peaks, dtype=bool)
    peaks_new[tuple(inds.T)] = True
    return peaks_new


def trim_saddle_points(peaks, dt, max_iters=10):
    r"""
    Removes peaks that were mistakenly identified because they lied on a
    saddle or ridge in the distance transform that was not actually a true
    local peak.

    Parameters
    ----------
    peaks : ND-array
        A boolean image containing True values to mark peaks in the distance
        transform (``dt``)

    dt : ND-array
        The distance transform of the pore space for which the true peaks are
        sought.

    max_iters : int
        The maximum number of iterations to run while eroding the saddle
        points.  The default is 10, which is usually not reached; however,
        a warning is issued if the loop ends prior to removing all saddle
        points.

    Returns
    -------
    image : ND-array
        An image with fewer peaks than the input image
    """
    peaks = sp.copy(peaks)
    if dt.ndim == 2:
        from skimage.morphology import square as cube
    else:
        from skimage.morphology import cube
    labels, N = spim.label(peaks)
    slices = spim.find_objects(labels)
    for i in range(N):
        s = extend_slice(s=slices[i], shape=peaks.shape, pad=10)
        peaks_i = labels[s] == i+1
        dt_i = dt[s]
        im_i = dt_i > 0
        iters = 0
        peaks_dil = sp.copy(peaks_i)
        while iters < max_iters:
            iters += 1
            peaks_dil = spim.binary_dilation(input=peaks_dil,
                                             structure=cube(3))
            peaks_max = peaks_dil*sp.amax(dt_i*peaks_dil)
            peaks_extended = (peaks_max == dt_i)*im_i
            if sp.all(peaks_extended == peaks_i):
                break  # Found a true peak
            elif sp.sum(peaks_extended*peaks_i) == 0:
                peaks_i = False
                break  # Found a saddle point
        peaks[s] = peaks_i
        if iters >= max_iters:
            print('Maximum number of iterations reached, consider'
                  + 'running again with a larger value of max_iters')
    return peaks


def trim_nearby_peaks(peaks, dt):
    r"""
    Finds pairs of peaks that are nearer to each other than to the solid phase,
    and removes the peak that is closer to the solid.

    Parameters
    ----------
    peaks : ND-array
        A boolean image containing True values to mark peaks in the distance
        transform (``dt``)

    dt : ND-array
        The distance transform of the pore space for which the true peaks are
        sought.

    Returns
    -------
    image : ND-array
        An array the same size as ``peaks`` containing a subset of the peaks
        in the original image.

    Notes
    -----
    Each pair of peaks is considered simultaneously, so for a triplet of peaks
    each pair is considered.  This ensures that only the single peak that is
    furthest from the solid is kept.  No iteration is required.
    """
    peaks = sp.copy(peaks)
    if dt.ndim == 2:
        from skimage.morphology import square as cube
    else:
        from skimage.morphology import cube
    peaks, N = spim.label(peaks, structure=cube(3))
    crds = spim.measurements.center_of_mass(peaks, labels=peaks,
                                            index=sp.arange(1, N+1))
    crds = sp.vstack(crds).astype(int)  # Convert to numpy array of ints
    # Get distance between each peak as a distance map
    tree = sptl.cKDTree(data=crds)
    temp = tree.query(x=crds, k=2)
    nearest_neighbor = temp[1][:, 1]
    dist_to_neighbor = temp[0][:, 1]
    del temp, tree  # Free-up memory
    dist_to_solid = dt[tuple(crds.T)]  # Get distance to solid for each peak
    hits = sp.where(dist_to_neighbor < dist_to_solid)[0]
    # Drop peak that is closer to the solid than it's neighbor
    drop_peaks = []
    for peak in hits:
        if dist_to_solid[peak] < dist_to_solid[nearest_neighbor[peak]]:
            drop_peaks.append(peak)
        else:
            drop_peaks.append(nearest_neighbor[peak])
    drop_peaks = sp.unique(drop_peaks)
    # Remove peaks from image
    slices = spim.find_objects(input=peaks)
    for s in drop_peaks:
        peaks[slices[s]] = 0
    return (peaks > 0)


def find_disconnected_voxels(im, conn=None):
    r"""
    This identifies all pore (or solid) voxels that are not connected to the
    edge of the image.  This can be used to find blind pores, or remove
    artifacts such as solid phase voxels that are floating in space.

    Parameters
    ----------
    im : ND-image
        A Boolean image, with True values indicating the phase for which
        disconnected voxels are sought.

    conn : int
        For 2D the options are 4 and 8 for square and diagonal neighbors, while
        for the 3D the options are 6 and 26, similarily for square and diagonal
        neighbors.  The default is max

    Returns
    -------
    image : ND-array
        An ND-image the same size as ``im``, with True values indicating
        voxels of the phase of interest (i.e. True values in the original
        image) that are not connected to the outer edges.

    Notes
    -----
    image : ND-array
        The returned array (e.g. ``holes``) be used to trim blind pores from
        ``im`` using: ``im[holes] = False``

    """
    if im.ndim == 2:
        if conn == 4:
            strel = disk(1)
        elif conn in [None, 8]:
            strel = square(3)
    elif im.ndim == 3:
        if conn == 6:
            strel = ball(1)
        elif conn in [None, 26]:
            strel = cube(3)
    labels, N = spim.label(input=im, structure=strel)
    holes = clear_border(labels=labels) > 0
    return holes


def fill_blind_pores(im):
    r"""
    Fills all pores that are not connected to the edges of the image.

    Parameters
    ----------
    im : ND-array
        The image of the porous material

    Returns
    -------
    image : ND-array
        A version of ``im`` but with all the disconnected pores removed.

    See Also
    --------
    find_disconnected_voxels

    """
    im = sp.copy(im)
    holes = find_disconnected_voxels(im)
    im[holes] = False
    return im


def trim_floating_solid(im):
    r"""
    Removes all solid that that is not attached to the edges of the image.

    Parameters
    ----------
    im : ND-array
        The image of the porous material

    Returns
    -------
    image : ND-array
        A version of ``im`` but with all the disconnected solid removed.

    See Also
    --------
    find_disconnected_voxels

    """
    im = sp.copy(im)
    holes = find_disconnected_voxels(~im)
    im[holes] = True
    return im


def trim_nonpercolating_paths(im, inlet_axis=0, outlet_axis=0):
    r"""
    Removes all nonpercolating paths between specified edges

    This function is essential when performing transport simulations on an
    image, since image regions that do not span between the desired inlet and
    outlet do not contribute to the transport.

    Parameters
    ----------
    im : ND-array
        The image of the porous material with ```True`` values indicating the
        phase of interest

    inlet_axis : int
        Inlet axis of boundary condition. For three dimensional image the
        number ranges from 0 to 2. For two dimensional image the range is
        between 0 to 1.

    outlet_axis : int
        Outlet axis of boundary condition. For three dimensional image the
        number ranges from 0 to 2. For two dimensional image the range is
        between 0 to 1.

    Returns
    -------
    image : ND-array
        A copy of ``im`` with all the nonpercolating paths removed

    See Also
    --------
    find_disconnected_voxels
    trim_floating_solid
    trim_blind_pores

    """
    im = trim_floating_solid(~im)
    labels = spim.label(~im)[0]
    inlet = sp.zeros_like(im, dtype=int)
    outlet = sp.zeros_like(im, dtype=int)
    if im.ndim == 3:
        if inlet_axis == 0:
            inlet[0, :, :] = 1
        elif inlet_axis == 1:
            inlet[:, 0, :] = 1
        elif inlet_axis == 2:
            inlet[:, :, 0] = 1

        if outlet_axis == 0:
            outlet[-1, :, :] = 1
        elif outlet_axis == 1:
            outlet[:, -1, :] = 1
        elif outlet_axis == 2:
            outlet[:, :, -1] = 1

    if im.ndim == 2:
        if inlet_axis == 0:
            inlet[0, :] = 1
        elif inlet_axis == 1:
            inlet[:, 0] = 1

        if outlet_axis == 0:
            outlet[-1, :] = 1
        elif outlet_axis == 1:
            outlet[:, -1] = 1
    IN = sp.unique(labels*inlet)
    OUT = sp.unique(labels*outlet)
    new_im = sp.isin(labels, list(set(IN) ^ set(OUT)), invert=True)
    im[new_im == 0] = True
    return ~im


def trim_extrema(im, h, mode='maxima'):
    r"""
    Trims local extrema in greyscale values by a specified amount.

    This essentially decapitates peaks and/or floods valleys.

    Parameters
    ----------
    im : ND-array
        The image whose extrema are to be removed

    h : float
        The height to remove from each peak or fill in each valley

    mode : string {'maxima' | 'minima' | 'extrema'}
        Specifies whether to remove maxima or minima or both

    Returns
    -------
    image : ND-array
        A copy of the input image with all the peaks and/or valleys removed.

    Notes
    -----
    This function is referred to as **imhmax** or **imhmin** in Matlab.

    """
    result = im
    if mode in ['maxima', 'extrema']:
        result = reconstruction(seed=im - h, mask=im, method='dilation')
    elif mode in ['minima', 'extrema']:
        result = reconstruction(seed=im + h, mask=im, method='erosion')
    return result


@jit(forceobj=True)
def flood(im, regions=None, mode='max'):
    r"""
    Floods/fills each region in an image with a single value based on the
    specific values in that region.  The ``mode`` argument is used to
    determine how the value is calculated.

    Parameters
    ----------
    im : array_like
        An ND image with isolated regions containing 0's elsewhere.

    regions : array_like
        An array the same shape as ``im`` with each region labeled.  If None is
        supplied (default) then ``scipy.ndimage.label`` is used with its
        default arguments.

    mode : string
        Specifies how to determine which value should be used to flood each
        region.  Options are:

        'max' - Floods each region with the local maximum in that region

        'min' - Floods each region the local minimum in that region

        'size' - Floods each region with the size of that region

    Returns
    -------
    image : ND-array
        A copy of ``im`` with new values placed in each forground voxel based
        on the ``mode``.

    See Also
    --------
    props_to_image

    """
    mask = im > 0
    if regions is None:
        labels, N = spim.label(mask)
    else:
        labels = sp.copy(regions)
        N = labels.max()
    I = im.flatten()
    L = labels.flatten()
    if mode.startswith('max'):
        V = sp.zeros(shape=N+1, dtype=float)
        for i in range(len(L)):
            if V[L[i]] < I[i]:
                V[L[i]] = I[i]
    elif mode.startswith('min'):
        V = sp.ones(shape=N+1, dtype=float)*sp.inf
        for i in range(len(L)):
            if V[L[i]] > I[i]:
                V[L[i]] = I[i]
    elif mode.startswith('size'):
        V = sp.zeros(shape=N+1, dtype=int)
        for i in range(len(L)):
            V[L[i]] += 1
    im_flooded = sp.reshape(V[labels], newshape=im.shape)
    im_flooded = im_flooded*mask
    return im_flooded


def find_dt_artifacts(dt):
    r"""
    Finds points in a distance transform that are closer to wall than solid.

    These points could *potentially* be erroneously high since their distance
    values do not reflect the possibility that solid may have been present
    beyond the border of the image but lost by trimming.

    Parameters
    ----------
    dt : ND-array
        The distance transform of the phase of interest

    Returns
    -------
    image : ND-array
        An ND-array the same shape as ``dt`` with numerical values indicating
        the maximum amount of error in each volxel, which is found by
        subtracting the distance to nearest edge of image from the distance
        transform value. In other words, this is the error that would be found
        if there were a solid voxel lurking just beyond the nearest edge of
        the image.  Obviously, voxels with a value of zero have no error.

    """
    temp = sp.ones(shape=dt.shape)*sp.inf
    for ax in range(dt.ndim):
        dt_lin = distance_transform_lin(sp.ones_like(temp, dtype=bool),
                                        axis=ax, mode='both')
        temp = sp.minimum(temp, dt_lin)
    result = sp.clip(dt - temp, a_min=0, a_max=sp.inf)
    return result


def region_size(im):
    r"""
    Replace each voxel with size of region to which it belongs

    Parameters
    ----------
    im : ND-array
        Either a boolean image wtih ``True`` indicating the features of
        interest, in which case ``scipy.ndimage.label`` will be applied to
        find regions, or a greyscale image with integer values indicating
        regions.

    Returns
    -------
    image : ND-array
        A copy of ``im`` with each voxel value indicating the size of the
        region to which it belongs.  This is particularly useful for finding
        chord sizes on the image produced by ``apply_chords``.
    """
    if im.dtype == bool:
        im = spim.label(im)[0]
    counts = sp.bincount(im.flatten())
    counts[0] = 0
    chords = counts[im]
    return chords


def apply_chords(im, spacing=1, axis=0, trim_edges=True, label=False):
    r"""
    Adds chords to the void space in the specified direction.  The chords are
    separated by 1 voxel plus the provided spacing.

    Parameters
    ----------
    im : ND-array
        An image of the porous material with void marked as ``True``.

    spacing : int
        Separation between chords.  The default is 1 voxel.  This can be
        decreased to 0, meaning that the chords all touch each other, which
        automatically sets to the ``label`` argument to ``True``.

    axis : int (default = 0)
        The axis along which the chords are drawn.

    trim_edges : bool (default = ``True``)
        Whether or not to remove chords that touch the edges of the image.
        These chords are artifically shortened, so skew the chord length
        distribution.

    label : bool (default is ``False``)
        If ``True`` the chords in the returned image are each given a unique
        label, such that all voxels lying on the same chord have the same
        value.  This is automatically set to ``True`` if spacing is 0, but is
        ``False`` otherwise.

    Returns
    -------
    image : ND-array
        A copy of ``im`` with non-zero values indicating the chords.

    See Also
    --------
    apply_chords_3D

    """
    if spacing < 0:
        raise Exception('Spacing cannot be less than 0')
    if spacing == 0:
        label = True
    result = sp.zeros(im.shape, dtype=int)  # Will receive chords at end
    slxyz = [slice(None, None, spacing*(axis != i) + 1) for i in [0, 1, 2]]
    slices = tuple(slxyz[:im.ndim])
    s = [[0, 1, 0], [0, 1, 0], [0, 1, 0]]  # Straight-line structuring element
    if im.ndim == 3:  # Make structuring element 3D if necessary
        s = sp.pad(sp.atleast_3d(s), pad_width=((0, 0), (0, 0), (1, 1)),
                   mode='constant', constant_values=0)
    im = im[slices]
    s = sp.swapaxes(s, 0, axis)
    chords = spim.label(im, structure=s)[0]
    if trim_edges:  # Label on border chords will be set to 0
        chords = clear_border(chords)
    result[slices] = chords  # Place chords into empty image created at top
    if label is False:  # Remove label if not requested
        result = result > 0
    return result


def apply_chords_3D(im, spacing=0, trim_edges=True):
    r"""
    Adds chords to the void space in all three principle directions.  The
    chords are seprated by 1 voxel plus the provided spacing.  Chords in the X,
    Y and Z directions are labelled 1, 2 and 3 resepctively.

    Parameters
    ----------
    im : ND-array
        A 3D image of the porous material with void space marked as True.

    spacing : int (default = 0)
        Chords are automatically separed by 1 voxel on all sides, and this
        argument increases the separation.

    trim_edges : bool (default is ``True``)
        Whether or not to remove chords that touch the edges of the image.
        These chords are artifically shortened, so skew the chord length
        distribution

    Returns
    -------
    image : ND-array
        A copy of ``im`` with values of 1 indicating x-direction chords,
        2 indicating y-direction chords, and 3 indicating z-direction chords.

    Notes
    -----
    The chords are separated by a spacing of at least 1 voxel so that tools
    that search for connected components, such as ``scipy.ndimage.label`` can
    detect individual chords.

    See Also
    --------
    apply_chords

    """
    if im.ndim < 3:
        raise Exception('Must be a 3D image to use this function')
    if spacing < 0:
        raise Exception('Spacing cannot be less than 0')
    ch = sp.zeros_like(im, dtype=int)
    ch[:, ::4+2*spacing, ::4+2*spacing] = 1  # X-direction
    ch[::4+2*spacing, :, 2::4+2*spacing] = 2  # Y-direction
    ch[2::4+2*spacing, 2::4+2*spacing, :] = 3  # Z-direction
    chords = ch*im
    if trim_edges:
        temp = clear_border(spim.label(chords > 0)[0]) > 0
        chords = temp*chords
    return chords


def local_thickness(im, sizes=25, mode='hybrid'):
    r"""
    For each voxel, this functions calculates the radius of the largest sphere
    that both engulfs the voxel and fits entirely within the foreground. This
    is not the same as a simple distance transform, which finds the largest
    sphere that could be *centered* on each voxel.

    Parameters
    ----------
    im : array_like
        A binary image with the phase of interest set to True

    sizes : array_like or scalar
        The sizes to invade.  If a list of values of provided they are used
        directly.  If a scalar is provided then that number of points spanning
        the min and max of the distance transform are used.

    mode : string
        Controls with method is used to compute the result.  Options are:

        'hybrid' - (default) Performs a distance tranform of the void space,
        thresholds to find voxels larger than ``sizes[i]``, trims the resulting
        mask if ``access_limitations`` is ``True``, then dilates it using the
        efficient fft-method to obtain the non-wetting fluid configuration.

        'dt' - Same as 'hybrid', except uses a second distance transform,
        relative to the thresholded mask, to find the invading fluid
        configuration.  The choice of 'dt' or 'hybrid' depends on speed, which
        is system and installation specific.

        'mio' - Using a single morphological image opening step to obtain the
        invading fluid confirguration directly, *then* trims if
        ``access_limitations`` is ``True``.  This method is not ideal and is
        included mostly for comparison purposes.

    Returns
    -------
    image : ND-array
        A copy of ``im`` with the pore size values in each voxel

    Notes
    -----
    The term *foreground* is used since this function can be applied to both
    pore space or the solid, whichever is set to True.

    This function is identical to porosimetry with ``access_limited`` set to
    ``False``.

    """
    im_new = porosimetry(im=im, sizes=sizes, access_limited=False, mode=mode)
    return im_new


def porosimetry(im, sizes=25, inlets=None, access_limited=True,
                mode='hybrid'):
    r"""
    Performs a porosimetry simulution on the image

    Parameters
    ----------
    im : ND-array
        An ND image of the porous material containing True values in the
        pore space.

    sizes : array_like or scalar
        The sizes to invade.  If a list of values of provided they are used
        directly.  If a scalar is provided then that number of points spanning
        the min and max of the distance transform are used.

    inlets : ND-array, boolean
        A boolean mask with True values indicating where the invasion
        enters the image.  By default all faces are considered inlets,
        akin to a mercury porosimetry experiment.  Users can also apply
        solid boundaries to their image externally before passing it in,
        allowing for complex inlets like circular openings, etc.  This argument
        is only used if ``access_limited`` is ``True``.

    access_limited : Boolean
        This flag indicates if the intrusion should only occur from the
        surfaces (``access_limited`` is True, which is the default), or
        if the invading phase should be allowed to appear in the core of
        the image.  The former simulates experimental tools like mercury
        intrusion porosimetry, while the latter is useful for comparison
        to gauge the extent of shielding effects in the sample.

    mode : string
        Controls with method is used to compute the result.  Options are:

        'hybrid' - (default) Performs a distance tranform of the void space,
        thresholds to find voxels larger than ``sizes[i]``, trims the resulting
        mask if ``access_limitations`` is ``True``, then dilates it using the
        efficient fft-method to obtain the non-wetting fluid configuration.

        'dt' - Same as 'hybrid', except uses a second distance transform,
        relative to the thresholded mask, to find the invading fluid
        configuration.  The choice of 'dt' or 'hybrid' depends on speed, which
        is system and installation specific.

        'mio' - Using a single morphological image opening step to obtain the
        invading fluid confirguration directly, *then* trims if
        ``access_limitations`` is ``True``.  This method is not ideal and is
        included mostly for comparison purposes.  The morphological operations
        are done using fft-based method implementations.

    Returns
    -------
    image : ND-array
        A copy of ``im`` with voxel values indicating the sphere radius at
        which it becomes accessible from the inlets.  This image can be used
        to find invading fluid configurations as a function of applied
        capillary pressure by applying a boolean comparison:
        ``inv_phase = im > r`` where ``r`` is the radius (in voxels) of the
        invading sphere.  Of course, ``r`` can be converted to capillary
        pressure using your favorite model.

    See Also
    --------
    fftmorphology

    """
    def trim_blobs(im, inlets):
        temp = sp.zeros_like(im)
        temp[inlets] = True
        labels, N = spim.label(im + temp)
        im = im ^ (clear_border(labels=labels) > 0)
        return im

    dt = spim.distance_transform_edt(im > 0)

    if inlets is None:
        inlets = get_border(im.shape, mode='faces')
    inlets = sp.where(inlets)

    if isinstance(sizes, int):
        sizes = sp.logspace(start=sp.log10(sp.amax(dt)), stop=0, num=sizes)
    else:
        sizes = sp.sort(a=sizes)[-1::-1]

    if im.ndim == 2:
        strel = ps_disk
    else:
        strel = ps_ball

    imresults = sp.zeros(sp.shape(im))
    if mode == 'mio':
        pw = int(sp.floor(dt.max()))
        impad = sp.pad(im, mode='symmetric', pad_width=pw)
        imresults = sp.zeros(sp.shape(impad))
        for r in tqdm(sizes):
            imtemp = fftmorphology(impad, strel(r), mode='opening')
            if access_limited:
                imtemp = trim_blobs(imtemp, inlets)
            if sp.any(imtemp):
                imresults[(imresults == 0)*imtemp] = r
        if im.ndim == 2:
            imresults = imresults[pw:-pw, pw:-pw]
        else:
            imresults = imresults[pw:-pw, pw:-pw, pw:-pw]
    elif mode == 'dt':
        for r in tqdm(sizes):
            imtemp = dt >= r
            if access_limited:
                imtemp = trim_blobs(imtemp, inlets)
            if sp.any(imtemp):
                imtemp = spim.distance_transform_edt(~imtemp) < r
                imresults[(imresults == 0)*imtemp] = r
    elif mode == 'hybrid':
        for r in tqdm(sizes):
            imtemp = dt >= r
            if access_limited:
                imtemp = trim_blobs(imtemp, inlets)
            if sp.any(imtemp):
                imtemp = fftconvolve(imtemp, strel(r), mode='same') > 0.0001
                imresults[(imresults == 0)*imtemp] = r
    else:
        raise Exception('Unreckognized mode ' + mode)
    return imresults


def _get_axial_shifts(ndim=2, include_diagonals=False):
    r'''
    Helper function to generate the axial shifts that will be performed on
    the image to identify bordering pixels/voxels
    '''
    if ndim == 2:
        if include_diagonals:
            neighbors = square(3)
        else:
            neighbors = diamond(1)
        neighbors[1, 1] = 0
        x, y = np.where(neighbors)
        x -= 1
        y -= 1
        return np.vstack((x, y)).T
    else:
        if include_diagonals:
            neighbors = cube(3)
        else:
            neighbors = octahedron(1)
        neighbors[1, 1, 1] = 0
        x, y, z = np.where(neighbors)
        x -= 1
        y -= 1
        z -= 1
        return np.vstack((x, y, z)).T


def _make_stack(im, include_diagonals=False):
    r'''
    Creates a stack of images with one extra dimension to the input image
    with length equal to the number of borders to search + 1.
    Image is rolled along the axial shifts so that the border pixel is
    overlapping the original pixel. First image in stack is the original.
    Stacking makes direct vectorized array comparisons possible.
    '''
    ndim = len(np.shape(im))
    axial_shift = _get_axial_shifts(ndim, include_diagonals)
    if ndim == 2:
        stack = np.zeros([np.shape(im)[0],
                          np.shape(im)[1],
                          len(axial_shift)+1])
        stack[:, :, 0] = im
        for i in range(len(axial_shift)):
            ax0, ax1 = axial_shift[i]
            temp = np.roll(np.roll(im, ax0, 0), ax1, 1)
            stack[:, :, i+1] = temp
        return stack
    elif ndim == 3:
        stack = np.zeros([np.shape(im)[0],
                          np.shape(im)[1],
                          np.shape(im)[2],
                          len(axial_shift)+1])
        stack[:, :, :, 0] = im
        for i in range(len(axial_shift)):
            ax0, ax1, ax2 = axial_shift[i]
            temp = np.roll(np.roll(np.roll(im, ax0, 0), ax1, 1), ax2, 2)
            stack[:, :, :, i+1] = temp
        return stack


def nphase_border(im, include_diagonals=False):
    r'''
    Identifies the voxels in regions that border *N* other regions.

    Useful for finding triple-phase boundaries.

    Parameters
    ----------
    im : ND-array
        An ND image of the porous material containing discrete values in the
        pore space identifying different regions. e.g. the result of a
        snow-partition

    include_diagonals : boolean
        When identifying bordering pixels (2D) and voxels (3D) include those
        shifted along more than one axis

    Returns
    -------
    image : ND-array
        A copy of ``im`` with voxel values equal to the number of uniquely
        different bordering values
    '''
    # Get dimension of image
    ndim = len(np.shape(im))
    if ndim not in [2, 3]:
        raise NotImplementedError("Function only works for 2d and 3d images")
    # Pad image to handle edges
    im = np.pad(im, pad_width=1, mode='edge')
    # Stack rolled images for each neighbor to be inspected
    stack = _make_stack(im, include_diagonals)
    # Sort the stack along the last axis
    stack.sort()
    out = np.ones_like(im)
    # Run through stack recording when neighbor id changes
    # Number of changes is number of unique bordering regions
    for k in range(np.shape(stack)[ndim])[1:]:
        if ndim == 2:
            mask = stack[:, :, k] != stack[:, :, k-1]
        elif ndim == 3:
            mask = stack[:, :, :, k] != stack[:, :, :, k-1]
        out += mask
    # Un-pad
    if ndim == 2:
        return out[1:-1, 1:-1].copy()
    else:
        return out[1:-1, 1:-1, 1:-1].copy()

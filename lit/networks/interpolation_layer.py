# Copyright 2019 Image Analysis Lab, German Center for Neurodegenerative Diseases (DZNE), Bonn
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# IMPORTS
import typing as _T
from numbers import Number
from typing import Any

import numpy as np
import torch
from torch import nn, Tensor
from torch.nn import functional as _F

T_Scale = _T.TypeVar("T_Scale", _T.List[float], Tensor)
T_ScaleAll = _T.TypeVar("T_ScaleAll", _T.Sequence[float], Tensor, np.ndarray, float)


class _ZoomNd(nn.Module):
    """Abstract Class to perform a crop and interpolation on a (N+2)-dimensional Tensor respecting batch and channel.

    Attributes
    ----------
    _mode
        interpolation mode as in `torch.nn.interpolate` (default: 'neareast')
    _target_shape
        Target tensor size for after this module,
        not including batchsize and channels.
    _N
        Number of dimensions

    Methods
    -------
    forward
        forward propagation
    _fix_scale_factors
        Checking and fixing the conformity of scale_factors
    _interpolate
        abstract method
    -calculate_crop_pad
        Return start- and end- coordinate
    """

    def __init__(
            self,
            interpolation_mode: str = "nearest"
    ):
        """Construct Zoom object.

        Parameters
        ----------
            Target tensor size for after this module,
            not including batchsize and channels.
        interpolation_mode : str
            interpolation mode as in `torch.nn.interpolate`
            (default: 'neareast')

        """
        super(_ZoomNd, self).__init__()
        self._mode = interpolation_mode
        if not hasattr(self, "_N"):
            self._N = -1


    def forward(
            self,
            input_tensor: Tensor,
            scale_factors: T_ScaleAll,
            target_shape: _T.Optional[_T.Sequence[int]] = None,
            rescale: bool = False
    ) -> _T.Tuple[Tensor, _T.List[T_Scale]]:
        """Zoom the `input_tensor` with `scale_factors`.

        This is not an exact zoom, but rather an "approximate zoom".
        This is due to the fact that the backbone function only interpolates between integer-sized images and therefore
        the target shape must be rounded to the nearest integer

        Parameters
        ----------
        input_tensor : Tensor
            The tensor of shape (N, C, D_1, ...D_{dim}), where N is the batch size, C is the number of channels
            and D_1, ..., D_{dim} are the dimensions of the image.
        scale_factors : T_ScaleAll
            The factor, by which to zoom the image. Can be a torch.Tensor or an array_like (numpy.ndarray
            or a (cascaded) sequence of floats or ints) or a float. If it is a float, all axis and all images of the
            batch are treated the same (zoomed by the float). Else, it will be interpreted as a multidimensional
            image: The first dimension corresponds to and must be equal to the batch size of the image. The second
            dimension is optional and may contain different values for the _scale_limits factor per axis. In consequence,
            this dimension can have 1 or {dim} values.
        rescale : bool
            (Default value = False)

        Returns
        -------
        _T.Tuple[Tensor, _T.List[T_Scale]]
            The zoomed tensor and the zoom factors that were actually used in the calculation for correct rescaling.

        Notes
        -------
        If this Module is used to zoom images of different voxelsizes to the same voxelsize, then `scale_factor`
        should be equal to `target_voxelsize / source_voxelsize`.

        """
        if self._N == -1:
            raise RuntimeError(
                "Direct instantiation of _InterpolateNd is not supported."
            )

        if input_tensor.dim() != 2 + self._N:
            raise ValueError(
                f"Expected {self._N+2}-dimensional input tensor, got {input_tensor.dim()}"
            )

        if len(target_shape) == 0:
            raise AttributeError(
                "The target_shape was not set, but a valid value is required."
            )

        scales_chunks = list(
            zip(*self._fix_scale_factors(scale_factors, input_tensor.shape[0]))
        )
        if len(scales_chunks) == 0:
            raise ValueError(
                f"Invalid scale_factors {scale_factors}, no chunks returned."
            )
        scales, chunks = map(list, scales_chunks)

        interp, scales_out = [], []

        # Pytorch Tensor shape BxCxHxW --> loop over batches, interpolate single images, concatenate output at end
        for tensor, scale_f, num in zip(
            torch.split(input_tensor, chunks, dim=0), scales, chunks
        ):
            if rescale:
                if isinstance(scale_f, list):
                    scale_f = [1 / sf for sf in scale_f]
                else:
                    scale_f = torch.div(1, scale_f)
            image, sf = self._interpolate(tensor, scale_f, target_shape)
            interp.append(image)
            scales_out.extend([sf] * num)

        return torch.cat(interp, dim=0), scales_out

    def _fix_scale_factors(
            self,
            scale_factors: T_ScaleAll,
            batch_size: int
    ) -> _T.Iterable[_T.Tuple[T_Scale, int]]:
        """Check and fix the conformity of scale_factors.

        Parameters
        ----------
        scale_factors : T_ScaleAll
            scale factors to fix dimensions
        batch_size : int
            number of batches

        Yields
        ------
        _T.Iterable[_T.Tuple[T_Scale, int]]
            The next fixed scale factor

        Raises
        ------
        ValueError
            scale_factors is neither a _T.Iterable nor a Number

        """
        # if isinstance(scale_factors, (Tensor, np.ndarray)):
        #     batch_size_sf = scale_factors.shape[0]
        # elif isinstance(scale_factors, _T.Iterable):
        #     scale_factors = list(scale_factors)
        #     batch_size_sf = len(scale_factors)
        # else:
        #     batch_size_sf = None

        if isinstance(scale_factors, _T.Iterable):
            # if batch_size not in [1, batch_size_sf]:
            #     raise ValueError(
            #         "scale_factors is a Sequence, but not the same length as the batch-size."
            #     )
            num = 0
            last_sf: _T.Optional[T_Scale] = None
            # Loop over batches
            for i, sf in enumerate(scale_factors):
                if isinstance(sf, Number):
                    sf = [sf] * self._N
                else:
                    if isinstance(sf, (np.ndarray, Tensor)):
                        if isinstance(sf, Tensor) and sf.dim() == 0:
                            sf_dim = 1
                            sf = [sf] * self._N
                        else:
                            sf_dim = sf.shape[0]
                            if sf_dim == 1:
                                sf = [sf] * self._N
                    elif isinstance(sf, _T.Iterable):
                        sf = list(sf)
                        sf_dim = len(sf)
                        sf = sf * self._N if sf_dim == 1 else sf
                    else:
                        raise ValueError(
                            f"Invalid type of scale_factors[{i}]: Expected was an Iterable or a Number, "
                            f"but {type(sf).__name__} found."
                        )
                    if sf_dim != self._N:
                        raise ValueError(
                            f"Invalid format of scale_factors[{i}]: Sequence contains {sf_dim} "
                            f"scale factors, but only 1 or {self._N} are valid: {sf}."
                        )

                if last_sf is not None and any(l != t for l, t in zip(last_sf, sf)):
                    yield last_sf, num
                    # reset the counter
                    num = 0
                num += 1
                last_sf = sf
            if last_sf is not None:
                yield last_sf, num
        elif isinstance(scale_factors, Number):
            yield [scale_factors] * self._N, batch_size
        else:
            raise ValueError(
                "scale_factors is not the correct type, must be sequence of floats or float."
            )

    def _interpolate(self, *args) -> _T.Tuple[Tensor, T_Scale]:
        """Abstract method.

        Parameters
        ----------
        args
            placeholder

        """
        raise NotImplementedError

    def _calculate_crop_pad(
            self,
            in_shape: _T.Sequence[int],
            scale_factor: T_Scale,
            target_shape: _T.Optional[_T.Sequence[int]],
            dim: int, 
            alignment: str
    ) -> _T.Tuple[slice, T_Scale, _T.Tuple[int, int], int]:
        """Return start- and end- coordinate given sizes, the updated scale factor [MISSING].

        Parameters
        ----------
        in_shape : _T.Sequence[int]
            [MISSING]
        scale_factor : T_Scale
            [MISSING]
        dim : int
            dimension to be cropped
        alignment : str
            [MISSING]

        Returns
        -------
        _T.Tuple[slice,T_Scale,_T.Tuple[int,int],int]
            crop_slice, new scale_factor, padding, interp_target_shape
        """
        this_in_shape = in_shape[dim + 2]
        source_size = target_shape[dim] * scale_factor[dim]

        # default no-cropping values for start and end
        start = 0
        end = this_in_shape

        # calculate out and rescale_factor
        if alignment == "mid" and (this_in_shape - int(source_size)) % 2 != 0:
            out = int(source_size / 2) * 2
        else:
            out = int(source_size)

        rescale_factor = out / target_shape[dim]

        if out > this_in_shape:
            # set distribution of padding for before and after
            if alignment == "from_end":
                padding = (1.0, 0.0)
            elif alignment == "mid":
                padding = (0.5, 0.5)
            else:
                padding = (0.0, 1.0)

            interp_target_shape = int(this_in_shape / scale_factor[dim])

            if (alignment == "mid" and (interp_target_shape - target_shape[dim]) % 2 != 0):
                raise NotImplementedError("not tested yet!")
                interp_target_shape = interp_target_shape - 1

            rescale_factor = this_in_shape / interp_target_shape

            # update padding to match full_pad
            full_pad = target_shape[dim] - interp_target_shape
            padding = tuple(int(p * full_pad) for p in padding)
        else:
            # update cropping to fit alignment
            if alignment == "mid":
                start = int((end - out) / 2)
            if alignment == "from_end":
                start = end - out
            else:
                end = start + out

            # default values for padding and target_shape
            padding = (0, 0)
            interp_target_shape = target_shape[dim]

        scale_factor[dim] = rescale_factor

        return slice(start, end), scale_factor, padding, interp_target_shape


class Zoom2d(_ZoomNd):
    """Perform a crop and interpolation on a Four-dimensional Tensor respecting batch and channel.

    Attributes
    ----------
     _N
        Number of dimensions (Here 2)
     _crop_position
        Position to crop

    Methods
    -------
    _interpolate
        Crops, interpolates and pads the tensor
    """

    def __init__(
            self,
            interpolation_mode: str = "nearest",
            crop_position: str = "top_left"
    ):
        """Construct Zoom2d object.

        Parameters
        ----------
        target_shape : _T.Optional[_T.Sequence[int]]
            Target tensor size for after this module, not including batchsize and channels.
        interpolation_mode : str
            interpolation mode as in `torch.nn.interpolate` (default: 'nearest')
        crop_position : str
            crop position to use from 'top_left', 'bottom_left', top_right', 'bottom_right',
            'center' (default: 'top_left')

        """
        if interpolation_mode not in ["nearest", "bilinear", "bicubic", "area"]:
            raise ValueError(f"invalid interpolation_mode, got {interpolation_mode}")

        if crop_position not in [
            "top_left",
            "bottom_left",
            "top_right",
            "bottom_right",
            "center",
        ]:
            raise ValueError(f"invalid crop_position, got {crop_position}")

        self._N = 2
        super(Zoom2d, self).__init__(interpolation_mode)
        self._crop_position = crop_position

    def _interpolate(
            self,
            data: Tensor,
            scale_factor: _T.Union[Tensor, np.ndarray, _T.Sequence[float]],
            target_shape: _T.Optional[_T.Sequence[int]]
    ) -> _T.Tuple[Tensor, T_Scale]:
        """Crop, interpolate and pad the tensor according to the scale_factor.

        Scale_factor must be 2-length sequence.

        Parameters
        ----------
        data : Tensor
            input, to-be-interpolated tensor
        scale_factor : _T.Union[Tensor, np.ndarray, _T.Sequence[float]]
            zoom factor
            Returns: the interpolated tensor

        Returns
        -------
        _T.Tuple[Tensor, T_Scale]
            The interpolated tensor and its scaling factor

        """
        scale_factor = (
            scale_factor.tolist()
            if isinstance(scale_factor, np.ndarray)
            else scale_factor
        )
        if isinstance(scale_factor, Tensor) and scale_factor.shape == (2,):
            pass
        elif isinstance(scale_factor, _T.Sequence) and len(scale_factor) == 2:
            scale_factor = list(scale_factor)
        else:
            raise ValueError(
                f"target_shape must be a ndarray, Tensor, or sequence of floats of length 2, received a "
                f"{type(scale_factor).__name__}: {scale_factor}."
            )

        if self._crop_position == "center":
            vertical_alignment, horizontal_alignment = ("mid",) * 2
        else:
            vertical_alignment, horizontal_alignment = ("from_start",) * 2

        if self._crop_position.startswith("bottom"):
            vertical_alignment = "from_end"

        if self._crop_position.endswith("right"):
            horizontal_alignment = "from_end"

        top_bottom, scale_factor, pad_tb, shape_tb = self._calculate_crop_pad(
            data.shape, scale_factor, target_shape, 0, vertical_alignment
        )
        left_right, scale_factor, pad_lr, shape_lr = self._calculate_crop_pad(
            data.shape, scale_factor, target_shape, 1, horizontal_alignment
        )

        data = data[:, :, top_bottom, left_right]

        if data.shape[2:] != (shape_tb, shape_lr):
            data = _F.interpolate(
                data,
                size=(shape_tb, shape_lr),
                mode=self._mode,
                align_corners=False,
            )

        # if padding is necessary, pad
        if pad_tb != (0, 0) or pad_lr != (0, 0):
            data = _F.pad(data, pad=pad_lr + pad_tb)

        return data, scale_factor


class Zoom3d(_ZoomNd):
    """Perform a crop and interpolation on a Five-dimensional Tensor respecting batch and channel."""

    def __init__(
            self,
            interpolation_mode: str = "nearest",
            crop_position: str = "front_top_left"
    ):
        """Construct Zoom3d object.

        Parameters
        ----------
        target_shape : _T.Optional[_T.Sequence[int]]
            Target tensor size for after this module,
            not including batchsize and channels.
        interpolation_mode : str
            interpolation mode as in `torch.nn.interpolate`
            (default: 'neareast')
        crop_position : str
            crop position to use from 'front_top_left', 'back_top_left',
            'front_bottom_left', 'back_bottom_left', 'front_top_right', 'back_top_right',
            'front_bottom_right', 'back_bottom_right', 'center' (default: 'front_top_left')

        """
        if interpolation_mode not in ["nearest", "trilinear", "area"]:
            raise ValueError(f"invalid interpolation_mode, got {interpolation_mode}")

        if crop_position not in [
            "front_top_left",
            "back_top_left",
            "front_bottom_left",
            "back_bottom_left",
            "front_top_right",
            "back_top_right",
            "front_bottom_right",
            "back_bottom_right",
            "center",
        ]:
            raise ValueError(f"invalid crop_position, got {crop_position}")

        self._N = 3
        super(Zoom3d, self).__init__(interpolation_mode)
        self._crop_position = crop_position

    def _interpolate(
            self,
            data: Tensor,
            scale_factor: _T.Union[Tensor, np.ndarray, _T.Sequence[int]],
            target_shape: _T.Optional[_T.Sequence[int]]
    ):
        """Crop, interpolate and pad the tensor according to the scale_factor.

        scale_factor must be 3-length sequence.

        Parameters
        ----------
        data : Tensor
            input, to-be-interpolated tensor
        scale_factor : _T.Sequence[int]
            zoom factor

        Returns
        -------
        _T.Tuple[Tensor, T_Scale]
             The interpolated tensor and its scaling factor
        """
        scale_factor = (
            scale_factor.tolist()
            if isinstance(scale_factor, np.ndarray)
            else scale_factor
        )
        if isinstance(scale_factor, Tensor) and scale_factor.shape == (3,):
            pass
        elif isinstance(scale_factor, _T.Sequence) and len(scale_factor) == 3:
            scale_factor = list(scale_factor)
        else:
            raise ValueError(
                f"target_shape must be a ndarray, Tensor, or sequence of floats of length 3, received a "
                f"{type(scale_factor).__name__}: {scale_factor}."
            )

        if self._crop_position == "center":
            depth_alignment, vertical_alignment, horizontal_alignment = ("mid",) * 3
        else:
            depth_alignment, vertical_alignment, horizontal_alignment = (
                "from_start",
            ) * 3

        if self._crop_position.startswith("back"):
            depth_alignment = "from_end"

        if "_bottom_" in self._crop_position:
            vertical_alignment = "from_end"

        if self._crop_position.endswith("right"):
            horizontal_alignment = "from_end"

        front_back, scale_factor, pad_fb, shape_fb = self._calculate_crop_pad(
            in_shape=data.shape, target_shape=target_shape, scale_factor=scale_factor, 
            dim=0, alignment=depth_alignment,
        )
        top_bottom, scale_factor, pad_tb, shape_tb = self._calculate_crop_pad(
            in_shape=data.shape, target_shape=target_shape, scale_factor=scale_factor, 
            dim=1, alignment=vertical_alignment
        )
        left_right, scale_factor, pad_lr, shape_lr = self._calculate_crop_pad(
            in_shape=data.shape, target_shape=target_shape, scale_factor=scale_factor, 
            dim=2, alignment=horizontal_alignment, 
        )

        #print('cropping', front_back, top_bottom, left_right)

        data = data[:, :, front_back, top_bottom, left_right]

        #print('shape after cropping', data.shape)
        #print('target shape for interpolation', shape_fb, shape_tb, shape_lr)

        if data.shape[2:] != (shape_fb, shape_tb, shape_lr):
            data = _F.interpolate(
                data,
                size=(shape_fb, shape_tb, shape_lr),
                mode=self._mode,
                align_corners=False if self._mode != 'nearest' else None,
            )

        # print('shape after interpolation', data.shape)
        # print('padding', pad_fb, pad_tb, pad_lr)

        # if padding is necessary, pad
        if pad_fb != (0, 0) or pad_tb != (0, 0) or pad_lr != (0, 0):
            data = _F.pad(data, pad=pad_fb + pad_lr + pad_tb)

        #print('shape after padding (output_shape)', data.shape)

        return data, scale_factor
    

class OnlyZoom3d(nn.Module):
    """Perform a crop and interpolation on a Five-dimensional Tensor respecting batch and channel."""

    def __init__(
            self,
            interpolation_mode: str = "nearest",
    ):
        """Construct Zoom3d object.

        Parameters
        ----------
        target_shape : _T.Optional[_T.Sequence[int]]
            Target tensor size for after this module,
            not including batchsize and channels.
        interpolation_mode : str
            interpolation mode as in `torch.nn.interpolate`
            (default: 'neareast')
        crop_position : str
            crop position to use from 'front_top_left', 'back_top_left',
            'front_bottom_left', 'back_bottom_left', 'front_top_right', 'back_top_right',
            'front_bottom_right', 'back_bottom_right', 'center' (default: 'front_top_left')

        """
        super(OnlyZoom3d, self).__init__()

        if interpolation_mode not in ["nearest", "trilinear", "area"]:
            raise ValueError(f"invalid interpolation_mode, got {interpolation_mode}")
        
        self.interpolation_mode = interpolation_mode
        
    
    def forward(self, input_tensor, out_shape):

        return _F.interpolate(input_tensor, size=out_shape, mode=self.interpolation_mode, align_corners=False,)

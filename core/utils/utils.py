import torch
import torch.nn.functional as F
import numpy as np
from scipy import interpolate


class InputPadder:
    """ Pads images such that dimensions are divisible by 8 """
    def __init__(self, dims, mode='sintel'):
        self.ht, self.wd = dims[-2:]
        pad_ht = (((self.ht // 8) + 1) * 8 - self.ht) % 8
        pad_wd = (((self.wd // 8) + 1) * 8 - self.wd) % 8
        if mode == 'sintel':
            self._pad = [pad_wd//2, pad_wd - pad_wd//2, pad_ht//2, pad_ht - pad_ht//2]
        elif mode == 'kitti400':
            self._pad = [0, 0, 0, 400 - self.ht]
        else:
            self._pad = [pad_wd//2, pad_wd - pad_wd//2, 0, pad_ht]

    def pad(self, *inputs):
        if sum(self._pad) == 0: return inputs
        return [F.pad(x, self._pad, mode='replicate') for x in inputs]

    def unpad(self,x):
        ht, wd = x.shape[-2:]
        c = [self._pad[2], ht-self._pad[3], self._pad[0], wd-self._pad[1]]
        return x[..., c[0]:c[1], c[2]:c[3]]

def forward_interpolate(flow):
    flow = flow.detach().cpu().numpy()
    dx, dy = flow[0], flow[1]

    ht, wd = dx.shape
    x0, y0 = np.meshgrid(np.arange(wd), np.arange(ht))

    x1 = x0 + dx
    y1 = y0 + dy
    
    x1 = x1.reshape(-1)
    y1 = y1.reshape(-1)
    dx = dx.reshape(-1)
    dy = dy.reshape(-1)

    valid = (x1 > 0) & (x1 < wd) & (y1 > 0) & (y1 < ht)
    x1 = x1[valid]
    y1 = y1[valid]
    dx = dx[valid]
    dy = dy[valid]

    flow_x = interpolate.griddata(
        (x1, y1), dx, (x0, y0), method='nearest', fill_value=0)

    flow_y = interpolate.griddata(
        (x1, y1), dy, (x0, y0), method='nearest', fill_value=0)

    flow = np.stack([flow_x, flow_y], axis=0)
    return torch.from_numpy(flow).float()

def bilinear_sampler(img: torch.Tensor, coords: torch.Tensor) -> torch.Tensor:
    """ Wrapper for grid_sample, uses pixel coordinates """
    H, W = img.shape[-2:]
    # NOTE: 2024-06-22 Breaking change to improve performance
    # Original will make grid a copy of coords, while new version makes a view
    # Original code:
    #   xgrid, ygrid = coords.split([1,1], dim=-1)
    #   xgrid = 2*xgrid/(W-1) - 1
    #   ygrid = 2*ygrid/(H-1) - 1
    #   grid = torch.cat([xgrid, ygrid], dim=-1)
    #   img = F.grid_sample(img, grid, align_corners=True)
    # New code seems to match performance of original codebase. 
    
    coords[..., 0] = 2 * coords[..., 0] / (W-1) - 1
    coords[..., 1] = 2 * coords[..., 1] / (H-1) - 1
    img = F.grid_sample(img, coords, align_corners=True)

    return img

# bilinear_sampler: Callable[[torch.Tensor, torch.Tensor,], torch.Tensor] = torch.jit.script(
#     __impl_bilinear_sampler, 
# )   #type: ignore
# NOTE: For debugging, use following to disable torchscript JIT
# bilinear_sampler = __impl_bilinear_sampler

def indexing(img, coords, mask=False):
    """ Wrapper for grid_sample, uses pixel coordinates """
    """
        TODO: directly indexing features instead of sampling
    """
    H, W = img.shape[-2:]
    xgrid, ygrid = coords.split([1,1], dim=-1)
    xgrid = 2*xgrid/(W-1) - 1
    ygrid = 2*ygrid/(H-1) - 1

    grid = torch.cat([xgrid, ygrid], dim=-1)
    img = F.grid_sample(img, grid, align_corners=True, mode='nearest')

    if mask:
        mask = (xgrid > -1) & (ygrid > -1) & (xgrid < 1) & (ygrid < 1)
        return img, mask.float()

    return img

def coords_grid(batch: int, ht: int, wd: int, device: torch.device):
    coords = torch.meshgrid(
        torch.arange(0, ht, device=device, dtype=torch.float),
        torch.arange(0, wd, device=device, dtype=torch.float),
        indexing="ij"
    )
    coords = torch.stack((coords[1], coords[0]), dim=0)
    return coords.unsqueeze(0).repeat(batch, 1, 1, 1)


def upflow8(flow, mode='bilinear'):
    new_size = (8 * flow.shape[2], 8 * flow.shape[3])
    return  8 * F.interpolate(flow, size=new_size, mode=mode, align_corners=True)

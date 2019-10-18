from numpy.testing import assert_allclose
import re
from pathlib import Path

try:
    import cupy
except ImportError:
    cupy = None


kernel_re = re.compile(r"extern \"C\" __global__.+?(?=extern|$)", re.DOTALL)
name_re = re.compile(r"(?<=void )\w+(?=\()")
def parse_kernels(src):
    kernels = {}
    for kernel in kernel_re.findall(src):
        name = name_re.search(kernel).group()
        kernels[name] = kernel
    return kernels


def compile_kernels(src):
    if cupy is None:
        return defaultdict(lambda: None)
    kernels = parse_kernels(src)
    return {name: cupy.RawKernel(src, name) for name, src in kernels.items()}

def compile_mmh(src):
    if cupy is None:
        return None
    return cupy.RawKernel(src, "hash_data")

    
SRC = (Path(__file__).parent / "_custom_kernels.cu").open().read()
KERNELS = compile_kernels(SRC)

MMH_SRC = (Path(__file__).parent / "_murmur3.cu").open().read()
KERNELS["hash"] = compile_mmh(MMH_SRC)

sum_pool_kernel = KERNELS["sum_pool"]
max_pool_kernel = KERNELS["max_pool"]
maxout_kernel = KERNELS["maxout"]
backprop_sum_pool_kernel = KERNELS["backprop_sum_pool"]
backprop_max_pool_kernel = KERNELS["backprop_max_pool"]
hash_data_kernel = compile_mmh(MMH_SRC)


def sum_pool(X, lengths, out=None, threads_per_block=128):
    if out is None:
        out = cupy.zeros((len(lengths), X.shape[1]), dtype="f")
    B = len(lengths)
    T = X.shape[0]
    O = X.shape[1]
    num_blocks = min(1, B // threads_per_block)
    sum_pool_kernel((num_blocks,), (threads_per_block,), (out, X, lengths, B, T, O))
    return out


def mean_pool(X, lengths, out=None, threads_per_block=128):
    if out is None:
        out = cupy.zeros((len(lengths), X.shape[1]), dtype="f")
    B = len(lengths)
    T = X.shape[0]
    O = X.shape[1]
    num_blocks = min(1, B // threads_per_block)
    sum_pool_kernel((num_blocks,), (threads_per_block,), (out, X, lengths, B, T, O))
    out /= lengths
    return out


def max_pool(X, lengths, out=None, threads_per_block=128):
    if out is None:
        maxes = cupy.zeros((len(lengths), X.shape[1]), dtype="f")
        which = cupy.zeros((len(lengths), X.shape[1]), dtype="i")
    else:
        maxes, which = out
    B = len(lengths)
    T = X.shape[0]
    O = X.shape[1]
    num_blocks = max(1, B // threads_per_block)
    max_pool_kernel((num_blocks,), (threads_per_block,),
        (maxes, which, X, lengths, B, T, O))
    return maxes, which


def backprop_sum_pool(d_sum, lengths, out=None, threads_per_block=128):
    B = len(lengths)
    T = int(lengths.sum())
    O = d_sum.shape[1]
    if out is None:
        out = cupy.zeros((T, O), dtype="f")

    num_blocks = max(1, T // threads_per_block)
    backprop_sum_pool_kernel((num_blocks,), (threads_per_block,),
        (out, d_sum, lengths, B, T, O))
    return out


def backprop_mean_pool(d_mean, lengths, out=None, threads_per_block=128):
    out = backprop_sum_pool(d_mean, lengths, out=out,
            threads_per_block=threads_per_block)
    out /= lengths
    return out


def backprop_max_pool(d_maxes, which, lengths, out=None, threads_per_block=128):
    B = len(lengths)
    T = int(lengths.sum())
    O = d_maxes.shape[1]
    if out is None:
        out = cupy.zeros((T, O), dtype="f")

    num_blocks = max(1, T // threads_per_block)
    backprop_max_pool_kernel((num_blocks,), (threads_per_block,),
        (out, d_maxes, which, lengths, B, T, O))
    return out


def hash(ids, seed, out=None, threads_per_block=128):
    if out is None:
        out = cupy.zeros((ids.shape[0], 4), dtype="uint32")
    # sizeof(uint32_t) * 4
    row_stride = 4 * 4
    col_stride = 8 # sizeof(uint64_t)
    T = ids.shape[0]

    num_blocks = max(1, T // threads_per_block)
    hash_data_kernel((num_blocks,), (threads_per_block,),
        (out, ids, row_stride, col_stride, ids.shape[0], seed))
    return out


def test_sum_pool():
    m = cupy.zeros((19, 5), dtype="f")
    m += 1
    lengths = cupy.array([5,5,3,6], dtype="i")
    output = sum_pool(m, lengths)
    assert output.sum() == m.sum(), (output.sum(), m.sum())


def test_max_pool():
    m = cupy.zeros((19, 5), dtype="f")
    m += cupy.random.uniform(-1, 1, m.shape)
    lengths = cupy.array([5,5,3,6], dtype="i")
    m[4, 0] = 1
    m[0, 1] = 2
    m[1, 3] = 3
    maxes, which = max_pool(m, lengths)
    start = 0
    for i, length in enumerate(lengths):
        truth = m[start:start+length].max(axis=0)
        assert_allclose(maxes[i].get(), truth.get())
        start += length


test_sum_pool()
test_max_pool()
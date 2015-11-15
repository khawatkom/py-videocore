from videocore.assembler import qpucode
from videocore.driver import Driver
import numpy as np

NUM_THREADS = 16

@qpucode
def helloworld(asm):
    setup_vpm_write()
    setup_dma_store(nrows = 1)

    # Add uniform[:,0] and SIMD element number
    iadd(vpm, uniform, element_number)

    # Write results back to host.
    start_dma_store(uniform)
    wait_dma_store()

    exit()

with Driver() as drv:
    prog     = drv.program(helloworld)
    result   = drv.array((NUM_THREADS, 16), 'uint32')
    uniforms = drv.array((NUM_THREADS,  2), 'uint32')

    uniforms[:, 0] = np.arange(0, NUM_THREADS*16, 16)
    uniforms[:, 1] = result.addresses[:, 0]
    prog(NUM_THREADS, uniforms, timeout=1000)
    print(result)

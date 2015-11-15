# QPU driver
# Copytirhg (c) 2015 Koichi Nakamura

from qpu.mailbox import MailBox
from qpu.assembler import assemble
import numpy as np
import os, mmap
import struct

DEFAULT_MAX_THREADS = 1024
DEFAULT_DATA_AREA_SIZE = 32 * 1024 * 1024
DEFAULT_CODE_AREA_SIZE = 1024 * 1024

class DriverError(Exception):
    "Exception related to QPU driver"
    pass

class Array(np.ndarray):
    def __new__(cls, *args, **kwargs):
        address = kwargs.pop('address')
        obj = super(Array, cls).__new__(cls, *args, **kwargs)
        obj.address = address
        obj.addresses = np.arange(
            obj.address,
            obj.address + obj.nbytes,
            obj.itemsize,
            np.uint32
            ).reshape(obj.shape)
        return obj

class Memory(object):
    def __init__(self, mailbox, size):
        self.size = size
        self.mailbox = mailbox
        self.handle  = None
        self.base  = None
        try:
            self.handle  = self.mailbox.allocate_memory(size, 4096,
                MailBox.MEM_FLAG_L1_NONALLOCATING)
            if self.handle == 0:
                raise DriverError('Failed to allocate QPU device memory')

            self.baseaddr = self.mailbox.lock_memory(self.handle)
            fd = os.open('/dev/mem', os.O_RDWR|os.O_SYNC)
            self.base = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE,
                    offset = self.baseaddr)
        except:
            if self.base:
                self.base.close()
            if self.handle:
                self.mailbox.unlock_memory(self.handle)
                self.mailbox.release_memory(self.handle)
            raise

    def close(self):
        self.base.close()
        self.mailbox.unlock_memory(self.handle)
        self.mailbox.release_memory(self.handle)

class Program(object):
    def __init__(self, driver, code_addr):
        self.driver    = driver
        self.code_addr = code_addr

    def __call__(self, *args, **kwargs):
        return self.driver.execute(self.code_addr, *args, **kwargs)

class Driver(object):
    def __init__(self,
            data_area_size = DEFAULT_DATA_AREA_SIZE,
            code_area_size = DEFAULT_CODE_AREA_SIZE,
            max_threads    = DEFAULT_MAX_THREADS
            ):
        self.mailbox = MailBox()
        self.mailbox.enable_qpu(1)
        self.memory  = None
        try:
            self.data_area_size = data_area_size
            self.code_area_size = code_area_size
            self.max_threads = max_threads

            self.code_area_base = 0
            self.data_area_base = self.code_area_base + self.code_area_size
            self.msg_area_base  = self.data_area_base + self.data_area_size

            self.code_pos = self.code_area_base
            self.data_pos = self.data_area_base

            total = data_area_size + code_area_size + max_threads * 64
            self.memory = Memory(self.mailbox, total)

            self.message = Array(
                    address = self.memory.baseaddr + self.msg_area_base,
                    buffer  = self.memory.base,
                    offset = self.msg_area_base,
                    shape = (self.max_threads, 2),
                    dtype = np.uint32)
        except:
            if self.memory:
                self.memory.close()
            self.mailbox.enable_qpu(0)
            self.mailbox.close()
            raise

    def close(self):
        self.memory.close()
        self.mailbox.enable_qpu(0)
        self.mailbox.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, value, traceback):
        self.close()
        return exc_type is None

    def array(self, *args, **kwargs):
        arr = Array(
                *args,
                address = self.memory.baseaddr + self.data_pos,
                buffer = self.memory.base,
                offset = self.data_pos,
                **kwargs)
        if self.data_pos + arr.nbytes > self.msg_area_base:
            raise DriverError('Array too large')
        self.data_pos += arr.nbytes
        return arr

    def program(self, program, *args, **kwargs):
        if hasattr(program, '__call__'):
            program = assemble(program, *args, **kwargs)
        code = memoryview(program).tobytes()
        if self.code_pos + len(code) > self.data_area_base:
            raise DriverError('Program too long')
        code_addr = self.memory.baseaddr + self.code_pos
        self.memory.base[self.code_pos:self.code_pos+len(code)] = code
        self.code_pos += len(code)
        return Program(self, code_addr)

    def execute(self, code_addr, num_threads, uniforms, timeout):
        if not (1 <= num_threads and num_threads <= self.max_threads):
            raise DriverError('num_threads must be in range (1 .. {})'.format(self.max_threads))
        self.message[:num_threads,0] = uniforms.addresses.reshape(num_threads, -1)[:,0]

        self.message[:num_threads,1] = code_addr
        r = self.mailbox.execute_qpu(num_threads, self.message.addresses[0, 0], 1, timeout)
        if r > 0:
            raise DriverError('QPU execution timeout')

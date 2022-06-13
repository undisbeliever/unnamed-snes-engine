# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


# Offset between the first_resource_bank and the named data banks
MS_FS_DATA_BANK_OFFSET = 0
ROOM_DATA_BANK_OFFSET = 1



class RomData:
    def __init__(self, addr, max_size):
        self._out = bytearray(max_size)

        self._view = memoryview(self._out)

        self._pos = 0
        self._addr = addr


    def data(self):
        return self._view[0:self._pos]


    def allocate(self, size):
        a = self._addr
        v = self._view[self._pos : self._pos + size]

        self._pos += size
        self._addr += size

        return v, a


    def insert_data(self, data):
        # ::TODO deduplicate data::
        size = len(data)

        a = self._addr
        self._view[self._pos : self._pos + size] = data

        self._pos += size
        self._addr += size

        return a


    def insert_data_addr_table(self, data_list):
        table_size = len(data_list) * 2
        table, table_addr = self.allocate(table_size)

        i = 0
        for d in data_list:
            addr = self.insert_data(d) & 0xffff

            table[i]   = addr & 0xff
            table[i+1] = addr >> 8

            i += 2

        assert i == table_size

        return table_addr




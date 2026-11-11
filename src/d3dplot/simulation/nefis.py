# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only reader for NEFIS 5 data and definition files (Delft3D 4 output).

Supports NEFIS 5 files with 8-byte addresses in little- or big-endian byte order,
as written by the Delft3D 4 suite (header flag ``L`` or ``B``). The layout follows
the Matlab reader ``vs_use.m`` / ``vs_let.m`` of the Delft3D suite.
"""

from __future__ import annotations

import itertools
import mmap
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np

_HEADER_BYTES = 60
_ADDRESS_BYTES = 8
_HASH_BUCKETS = 997
_HASH_TABLE_START = _HEADER_BYTES + _ADDRESS_BYTES
_NIL = 2**64 - 1
_GROUP_DATA_HEADER_BYTES = 392
_POINTER_SLOTS = 256
_TYPE_NAMES = ("CHARACTE", "COMPLEX", "INTEGER", "LOGICAL", "REAL")


@dataclass(frozen=True)
class NefisElement:
    """ELEMENT definition: one named array stored in a cell."""

    name: str
    quantity: str
    unit: str
    description: str
    type_name: str
    value_bytes: int
    size_bytes: int
    shape: tuple[int, ...]


@dataclass(frozen=True)
class NefisCell:
    """CELL definition: the elements stored per cell, in stored order."""

    name: str
    element_names: tuple[str, ...]


@dataclass(frozen=True)
class NefisGroupDefinition:
    """GROUP definition: the cell layout and dimensions of a group."""

    name: str
    cell_name: str
    size_dim: tuple[int, ...]
    order_dim: tuple[int, ...]


@dataclass(frozen=True)
class NefisGroup:
    """GROUP data record: one stored group instance (time series or constant)."""

    name: str
    def_name: str
    offset: int
    variable: bool
    size_dim: tuple[int, ...]


def _decode(raw: bytes) -> str:
    return raw.decode("latin-1").rstrip(" \x00")


def _byte_order(header: bytes, path: Path) -> str:
    text = header.decode("latin-1")
    if "NEFIS" not in text or "; 5." not in text:
        raise ValueError(f"{path}: not a NEFIS 5 file (header: {text.strip()!r})")
    flag = chr(header[_HEADER_BYTES - 1])
    if flag == "L":
        return "<"
    if flag == "B":
        return ">"
    raise ValueError(f"{path}: unsupported NEFIS address format flag {flag!r}")


class NefisFile:
    """Index of a NEFIS data file (``.dat``) and its definition file (``.def``).

    The definition is parsed once on open. Element values are read on request from a
    memory map of the data file, so one time step of a large file is read without
    loading the rest.
    """

    def __init__(
        self,
        data_path: str | os.PathLike[str],
        def_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.data_path = Path(data_path)
        self.def_path = (
            Path(def_path)
            if def_path is not None
            else self.data_path.with_suffix(".def")
        )
        self._handles: list[BinaryIO] = []
        self._maps: list[mmap.mmap] = []
        self._elements: dict[str, NefisElement] = {}
        self._cells: dict[str, NefisCell] = {}
        self._group_defs: dict[str, NefisGroupDefinition] = {}
        self._groups: dict[str, NefisGroup] = {}
        self._order = "<"
        try:
            def_buf = self._map(self.def_path)
            self._dat = self._map(self.data_path)
            self._order = _byte_order(bytes(def_buf[:_HEADER_BYTES]), self.def_path)
            if (
                _byte_order(bytes(self._dat[:_HEADER_BYTES]), self.data_path)
                != self._order
            ):
                raise ValueError(
                    f"{self.data_path}: byte order differs from {self.def_path}"
                )
            self._parse_definition(def_buf)
            self._parse_data(self._dat)
        except Exception:
            self.close()
            raise

    def __enter__(self) -> NefisFile:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        for buf in self._maps:
            buf.close()
        for handle in self._handles:
            handle.close()
        self._maps.clear()
        self._handles.clear()

    @property
    def groups(self) -> dict[str, NefisGroup]:
        return dict(self._groups)

    @property
    def elements(self) -> dict[str, NefisElement]:
        return dict(self._elements)

    def group_shape(self, group_name: str) -> tuple[int, ...]:
        """Dimensions of a group, with the variable dimension filled in."""
        return self._group(group_name).size_dim

    def read(
        self, group_name: str, element_name: str, index: int | None = None
    ) -> np.ndarray:
        """Read one element of a group.

        ``index`` is the 1-based index along the variable dimension and is required for
        variable groups. The result has the cell dimensions of the group followed by the
        element dimensions. Scalar elements come back with shape ``(1,)``.
        """
        group = self._group(group_name)
        definition = self._group_defs[group.def_name]
        cell = self._cells[definition.cell_name]
        if element_name not in cell.element_names:
            raise KeyError(
                f"element {element_name!r} is not stored in group {group_name!r}"
            )
        element = self._elements[element_name]
        element_offset = self._element_offset(cell, element_name)
        cell_bytes = self._cell_bytes(cell)

        if group.variable:
            if index is None:
                raise ValueError(
                    f"group {group_name!r} is variable; an index is required"
                )
            base = self._variable_block(group, index)
            if base is None:
                raise KeyError(f"group {group_name!r} has no data for index {index}")
        else:
            base = group.offset + _GROUP_DATA_HEADER_BYTES + 3 * _ADDRESS_BYTES

        vd = self._variable_dimension(definition)
        cell_dims = [i for i in range(len(group.size_dim)) if i != vd]
        cell_shape = tuple(group.size_dim[i] for i in cell_dims)
        strides = self._disk_strides(definition.order_dim, group.size_dim, vd)
        dtype = np.dtype(self._element_dtype(element))
        out = np.empty(cell_shape + element.shape, dtype=dtype)
        for position in itertools.product(*(range(n) for n in cell_shape)):
            linear = sum(
                p * strides[d] for p, d in zip(position, cell_dims, strict=True)
            )
            start = base + linear * cell_bytes + element_offset
            raw = self._dat[start : start + element.size_bytes]
            if len(raw) != element.size_bytes:
                raise ValueError(
                    f"{self.data_path}: element {element_name!r} lies outside the file"
                )
            out[position] = np.frombuffer(raw, dtype=dtype).reshape(
                element.shape, order="F"
            )
        return out

    def _map(self, path: Path) -> mmap.mmap:
        handle = path.open("rb")
        self._handles.append(handle)
        buf = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        self._maps.append(buf)
        return buf

    def _u8(self, buf: mmap.mmap, offset: int) -> int:
        return int(struct.unpack_from(self._order + "Q", buf, offset)[0])

    def _u4(self, buf: mmap.mmap, offset: int) -> int:
        return int(struct.unpack_from(self._order + "I", buf, offset)[0])

    def _bucket_records(self, buf: mmap.mmap, table_start: int) -> list[int]:
        """Offsets of all records in a hash table, following every bucket chain."""
        heads = struct.unpack_from(f"{self._order}{_HASH_BUCKETS}Q", buf, table_start)
        records: list[int] = []
        for head in heads:
            address = int(head)
            while address != _NIL:
                records.append(address)
                address = self._u8(buf, address)
        return records

    def _parse_definition(self, buf: mmap.mmap) -> None:
        if self._u8(buf, _HEADER_BYTES) != len(buf):
            raise ValueError(
                f"{self.def_path}: indicated file length does not match the file"
            )
        element_table = _HASH_TABLE_START
        cell_table = element_table + _HASH_BUCKETS * _ADDRESS_BYTES
        group_table = cell_table + _HASH_BUCKETS * _ADDRESS_BYTES
        for record in self._bucket_records(buf, element_table):
            self._add_element(buf, record)
        for record in self._bucket_records(buf, cell_table):
            self._add_cell(buf, record)
        for record in self._bucket_records(buf, group_table):
            self._add_group_definition(buf, record)

    def _add_element(self, buf: mmap.mmap, record: int) -> None:
        at = record + 3 * _ADDRESS_BYTES
        name = _decode(buf[at : at + 16])
        type_name = _decode(buf[at + 16 : at + 24])
        size_bytes = self._u8(buf, at + 24)
        value_bytes = self._u4(buf, at + 32)
        descript = _decode(buf[at + 36 : at + 132])
        ndim = self._u4(buf, at + 132)
        sizes = [self._u4(buf, at + 136 + 4 * i) for i in range(5)]
        if type_name not in _TYPE_NAMES:
            raise ValueError(
                f"{self.def_path}: element {name!r} has unsupported type {type_name!r}"
            )
        self._elements[name] = NefisElement(
            name=name,
            quantity=descript[0:16].strip(),
            unit=descript[16:32].strip(),
            description=descript[32:].strip(),
            type_name=type_name,
            value_bytes=value_bytes,
            size_bytes=size_bytes,
            shape=tuple(sizes[:ndim]) if ndim > 0 else (1,),
        )

    def _add_cell(self, buf: mmap.mmap, record: int) -> None:
        at = record + 3 * _ADDRESS_BYTES
        name = _decode(buf[at : at + 16])
        count = self._u4(buf, record + 4 * _ADDRESS_BYTES + 16)
        names_at = record + 4 * _ADDRESS_BYTES + 20
        element_names = tuple(
            _decode(buf[names_at + 16 * i : names_at + 16 * (i + 1)])
            for i in range(count)
        )
        self._cells[name] = NefisCell(name=name, element_names=element_names)

    def _add_group_definition(self, buf: mmap.mmap, record: int) -> None:
        at = record + 3 * _ADDRESS_BYTES
        name = _decode(buf[at : at + 16])
        cell_name = _decode(buf[at + 16 : at + 32])
        ndim = self._u4(buf, at + 32)
        if ndim == 0:
            size_dim: tuple[int, ...] = (1,)
            order_dim: tuple[int, ...] = (1,)
        else:
            size_dim = tuple(self._u4(buf, at + 36 + 4 * i) for i in range(ndim))
            order_dim = tuple(self._u4(buf, at + 56 + 4 * i) for i in range(ndim))
        self._group_defs[name] = NefisGroupDefinition(
            name=name,
            cell_name=cell_name,
            size_dim=size_dim,
            order_dim=order_dim,
        )

    def _parse_data(self, buf: mmap.mmap) -> None:
        for record in self._bucket_records(buf, _HASH_TABLE_START):
            at = record + 3 * _ADDRESS_BYTES
            code = _decode(buf[record + 2 * _ADDRESS_BYTES : at])
            name = _decode(buf[at : at + 16])
            def_name = _decode(buf[at + 16 : at + 32])
            if def_name not in self._group_defs:
                raise ValueError(
                    f"{self.data_path}: group {name!r} has no definition {def_name!r}"
                )
            definition = self._group_defs[def_name]
            variable = code.endswith("5")
            size_dim = definition.size_dim
            if variable:
                vd = self._variable_dimension(definition)
                size_dim = tuple(
                    self._variable_size(buf, record) if i == vd else n
                    for i, n in enumerate(size_dim)
                )
            self._groups[name] = NefisGroup(
                name=name,
                def_name=def_name,
                offset=record,
                variable=variable,
                size_dim=size_dim,
            )

    def _variable_size(self, buf: mmap.mmap, record: int) -> int:
        """Largest 1-based index along the variable dimension, from pointer tables."""
        table = record + _GROUP_DATA_HEADER_BYTES + 4 * _ADDRESS_BYTES
        size = 0
        for level in (3, 2, 1, 0):
            pointers = struct.unpack_from(f"{self._order}{_POINTER_SLOTS}Q", buf, table)
            used = [k for k, pointer in enumerate(pointers) if pointer != _NIL]
            if not used:
                break
            last = used[-1]
            if level == 0:
                size += last
            else:
                size += last * _POINTER_SLOTS**level
                table = int(pointers[last])
        return size

    def _variable_block(self, group: NefisGroup, index: int) -> int | None:
        """Offset of the data block for a 1-based index along the variable dimension."""
        table = group.offset + _GROUP_DATA_HEADER_BYTES + 4 * _ADDRESS_BYTES
        for level in (3, 2, 1):
            pointer = self._u8(self._dat, table + 8 * ((index >> (8 * level)) & 0xFF))
            if pointer == _NIL:
                return None
            table = pointer
        pointer = self._u8(self._dat, table + 8 * (index & 0xFF))
        return None if pointer == _NIL else pointer

    def _group(self, name: str) -> NefisGroup:
        if name not in self._groups:
            raise KeyError(f"group {name!r} is not in {self.data_path}")
        return self._groups[name]

    def _element_offset(self, cell: NefisCell, element_name: str) -> int:
        offset = 0
        for name in cell.element_names:
            if name == element_name:
                return offset
            offset += self._elements[name].size_bytes
        raise KeyError(element_name)

    def _cell_bytes(self, cell: NefisCell) -> int:
        return sum(self._elements[name].size_bytes for name in cell.element_names)

    @staticmethod
    def _variable_dimension(definition: NefisGroupDefinition) -> int:
        """Index of the variable dimension (size 0 in the definition), or -1 if none."""
        for i, n in enumerate(definition.size_dim):
            if n == 0:
                return i
        return -1

    @staticmethod
    def _disk_strides(
        order_dim: tuple[int, ...], size_dim: tuple[int, ...], vd: int
    ) -> list[int]:
        """Cell-index stride per dimension, first disk-order dimension fastest."""
        strides = [0] * len(size_dim)
        stride = 1
        for dim in order_dim:
            index = dim - 1
            if index == vd or not 0 <= index < len(size_dim):
                continue
            strides[index] = stride
            stride *= size_dim[index]
        return strides

    def _element_dtype(self, element: NefisElement) -> str:
        if element.type_name == "CHARACTE":
            return f"S{element.value_bytes}"
        if element.type_name == "REAL":
            return f"{self._order}f{element.value_bytes}"
        if element.type_name == "COMPLEX":
            return f"{self._order}c{element.value_bytes}"
        return f"{self._order}i{element.value_bytes}"

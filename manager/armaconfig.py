
import re, os, enum, functools, collections

from pathlib import Path
from typing import (
    Any,
    Union,
    Sequence
)

class Unexpected(Exception):
    def __init__(self, expected: list, got: str):
        super().__init__(f'Invalid character encountered: Expected ({expected}), got "{got}"')

def to_dict(data):
    dict_ = {}

    for i in data:
        if hasattr(i, 'to_dict'):
            dict_[i.name] = i.to_dict()
        else:
            dict_[i.name] = i.value

    return dict_

def encode(data):
    for i in data:
        yield from i.encode()

class A3Class:
    def __init__(self, name, inherits, body):
        self.name = name
        self.body = body
        
        if inherits is not None:
            pass
        else:
            self.inherits = None

    def to_dict(self):
        dict_ = to_dict(self.body)

        if self.inherits:
            return {
                **self.inherits.to_dict(),
                **dict_
            }

        return dict_

    def __getitem__(self, item):
        try:
            return next(x for x in self.body if x.name == item)
        except StopIteration:
            if not self.inherits:
                raise KeyError(item)

            return self.inherits.__getattr__(item)

    def encode(self):
        yield f'class {self.name}'

        if self.inherits:
            yield f': {self.inherits.name}'

        yield '{'
        yield from encode(self.body)

        yield '};'

    def __repr__(self):
        if self.body:
            body = ';'.join([str(x) for x in self.body]) + ';'
        else:
            body = ''

        return f'<{type(self).__name__} -> {self.name} : {self.inherits} {{ {body} }}>'

class A3Property:
    def __init__(self, name, value):
        self.name = name
        self.value = self._process_value(value)

    def __str__(self):
        return str(self.value)

    def encode_value(self, value):
        if isinstance(value, list):
            yield '{'
            yield ','.join([y for x in value for y in self.encode_value(x)])
            yield '}'
        elif isinstance(value, str):
            yield '"%s"' % re.sub(r'"', '""', value)
        else:
            yield str(value)

    def encode(self):
        yield self.name

        if isinstance(self.value, list):
            yield '[]'

        yield '='
        yield from self.encode_value(self.value)
        yield ';'

    def _process_value(self, value):
        if isinstance(value, list):
            return [self._process_value(x) for x in value if not isinstance(x, str) or x.strip()]
        else:
            value = value.strip()

            if value and value[0] == '"' and value[-1] == '"':
                value = value[1:len(value) - 1]

            try:
                new_val = float(value)

                if new_val.is_integer():
                    new_val = int(new_val)

                return new_val
            except ValueError:
                return value

    def __repr__(self):
        return f'<{type(self).__name__} -> {self.name} = {repr(self.value)}>'

class TokenType(enum.Enum):
    UNKNOWN = 0
    STRING = 1
    PREPRO = 2
    IDENTIFIER = 3

Token = collections.namedtuple('Token', ['type', 'lineno', 'value'])

class Scanner:
    def __init__(self, stream):
        self._stream = stream
        self._lines = self._stream.readlines()
        self._lineno = 0
        self._cursor = 0

    @property
    def line(self):
        return self._get_line(self._lineno)
        
    def _get_line(self, lineno):
        try:
            return self._lines[lineno]
        except IndexError:
            return ''

    def _advance(self, length=1):
        self._cursor += length
        
        line_length = len(self.line)

        if self._cursor >= line_length:
            self._cursor -= line_length
            self._lineno += 1

        if self._lineno >= len(self._lines):
            raise StopIteration

        return self

    def _peek(self, length=1):
        line_length = len(self.line)

        if self._cursor + length >= line_length:
            remainder = self._cursor + length - line_length
            line = self._get_line(self._lineno + 1)

            if remainder >= len(line):
                raise StopIteration

            return self.line[self._cursor:] + line[:remainder]

        return self.line[self._cursor:self._cursor + length]

    def _get_raw(self, length=1):
        seq = self._peek(length)

        self._advance(length)

        return seq

    def _find_delim(self, delim, advance=False):
        seq = ''
        length = len(delim)

        while self._peek(length) != delim:
            seq += self._get_raw(length)

        if advance:
            self._advance(length)

        return seq

    def _find_with_cb(self, callback, length=1, advance=False):
        seq = ''

        check = self._get_raw(length)

        while callback(check):
            seq += check
            check = self._get_raw(length)

        if not advance:
            self._advance(-length)

        return seq

    def _get_string(self):
        """
        This method assumes that the first " has been found
        """
        def callback(char):
            if char == '"':
                if self._peek() != '"':
                    return False

                self._advance(1)

            return True

        return self._find_with_cb(callback, length=1, advance=True)

    def is_identifier_char(self, char):
        return char.isalnum() or char == '_'

    def _iter_chars(self):
        while True:
            try:
                yield self._get_raw()
            except StopIteration:
                return

    def __iter__(self):
        return self

    def __next__(self):
        return self.scan()

    def scan(self, simple=False):
        for char in self._iter_chars():
            if char == '/' and ((peek := self._peek()) in ['/', '*']):
                if peek == '/':
                    self._find_delim('\n', advance=True)
                else:
                    self._find_delim('*/', advance=True)
            elif char == '#' and not self.line[:self._cursor].strip():
                yield Token(TokenType.PREPRO, self._lineno, None)
            elif char == '"':
                yield Token(TokenType.STRING, self._lineno, '"{}"'.format(self._get_string()))
            elif not simple and char == '_' or char.isalpha():
                yield Token(TokenType.IDENTIFIER, self._lineno, char + self._find_with_cb(self.is_identifier_char))
            else:
                yield Token(TokenType.UNKNOWN, self._lineno, char)

class ParserFactory(type):
    instances = {}

    def __call__(cls, file):
        fspath = os.fspath(file)

        if fspath in cls.instances:
            return cls.instances[fspath]

        cls.instances[fspath] = Parser.open_file(file)

        return cls.instances[fspath]

class Parser(metaclass=ParserFactory):
    def __init__(self, stream):
        self._stream = stream
        self._scanner = Scanner(stream)

        self.defined = {}
        self.links = []

    def _get_raw(self, include_ws=False):
        token = next(self._scanner.scan())

        if not include_ws and token.value.isspace():
            return self._get_raw(include_ws)

        return token

    def _get(self, length=1, expect_typ=None, expect_val=None, **kwargs):
        seq = [self._get_raw(**kwargs) for _ in range(length)]

        if expect_typ is not None:
            for i in range(len(expect_typ)):
                if seq[i].type != expect_typ[i]:
                    raise Unexpected(expected=expect_typ[i], got=seq[i].type)

        if expect_val is not None:
            for i in range(len(expect_val)):
                if seq[i].val != expect_val[i]:
                    raise Unexpected(expected=expect_val[i], got=seq[i].type)

        if length == 1:
            return seq[0]

        return seq

    def _expect_sequence(self, typ=None, val=None, **kwargs):
        assert None in (typ, val), '_expect_sequence: either typ or val has to be None'
        
        if typ is not None:
            for i in range(len(typ)):
                t, _, _ = self._get(**kwargs)

                if t != typ[i]:
                    raise Unexpected(expected=typ[i], got=t)

        elif val is not None:
            for i in range(len(val)):
                _, _, v = self._get(**kwargs)

                if v != val[i]:
                    raise Unexpected(expected=val[i], got=v)
        else:
            assert False, 'ok fuckhead'

    def _parse_value(self, is_array=False):
        seperators = ';,}' if is_array else ';'

        seq = ''

        _, _, v = self._get(1)

        if v == '{':
            seq = self._parse_value(True)

            _, _, v = self._get(1)
        else:
            while v not in seperators:
                seq += v
                _, _, v = self._get(1, include_ws=True)

            if not is_array: return seq

        seq = [seq]

        if v in ';,':
            return seq + self._parse_value(True)

        return seq

    def _parse_one(self, token=None):
        t, ln, val = token or self._get(1)

        if t == TokenType.IDENTIFIER:
            if val == 'class':
                _, _, name = self._get(1, expect_typ=[TokenType.IDENTIFIER])
                _, _, v = self._get(1, expect_typ=[TokenType.UNKNOWN])

                if v == ':':
                    inherits, opener = self._get(2, expect_typ=[TokenType.IDENTIFIER, TokenType.UNKNOWN])

                    inherits, opener = inherits.value, opener.value
                else:
                    inherits, opener = None, v

                if opener != '{': raise Unexpected(expected=['{'], got=v)

                body = []
                token = self._get(1)

                while not (token.type == TokenType.UNKNOWN and token.value == '}'):
                    body.append(next(self._parse_one(token)))
                    token = self._get(1)

                self._expect_sequence(val=';')

                yield A3Class(name, inherits, body)
            else:
                _, _, next_val = self._get(1, expect_typ=[TokenType.UNKNOWN])
                is_array = False

                if next_val == '[':
                    self._expect_sequence(val=[']', '=', '{'])
                    
                    is_array = True
                elif next_val != '=':
                    raise Unexpected(expected=['='], got=next_val)

                yield A3Property(val, self._parse_value(is_array))
        elif t == TokenType.UNKNOWN and val == ';':
            yield from self._parse_one()
        else:
            raise Unexpected(expected=[TokenType.IDENTIFIER], got=t)

    def parse(self):
        while True:
            try:
                yield from self._parse_one()
            except RuntimeError:
                return

    @classmethod
    def open_file(cls, file, *args, **kwargs):
        with open(file) as fp:
            self = super().__new__(cls)
            self.__init__(fp, *args, **kwargs)

            return self

if __name__ == '__main__':
    import json

    with open(Path.cwd().joinpath('config.githide.json'), 'w') as jp:
        parser = Parser(Path.cwd().joinpath('config.githide.cfg'))
        p1 = Parser(Path.cwd().joinpath('config.githide.cfg'))

        bitch = list(parser.parse())

        with open(Path.cwd().joinpath('output.githide.hpp'), 'w') as fp:
            for i in encode(bitch):
                fp.write(i)
                #if i == ';':
                    #fp.write('\n')

        json.dump(to_dict(bitch), jp, indent=4)

        print(bitch[-2]['Mission_1']['template'])
        print(parser is p1)
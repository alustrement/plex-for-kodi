from datetime import datetime

import exceptions
import util
import plexapp


# Search Types - Plex uses these to filter specific media types when searching.
SEARCHTYPES = {
    'movie': 1,
    'show': 2,
    'season': 3,
    'episode': 4,
    'artist': 8,
    'album': 9,
    'track': 10
}

LIBRARY_TYPES = {}


def registerLibType(cls):
    LIBRARY_TYPES[cls.TYPE] = cls
    return cls


class PlexValue(unicode):
    def __new__(cls, value, parent=None):
        self = super(PlexValue, cls).__new__(cls, value)
        self.parent = parent
        self.NA = False
        return self

    def __call__(self, default):
        return not self.NA and self or default

    def asBool(self):
        return self == '1'

    def asInt(self, default=0):
        return int(self or default)

    def asFloat(self, default=0):
        return float(self or default)

    def asDatetime(self, format_=None):
        if self.isdigit():
            dt = datetime.fromtimestamp(int(self))
        else:
            dt = datetime.strptime(self, '%Y-%m-%d')

        if not format_:
            return dt

        return dt.strftime(format_)

    def asURL(self):
        return self.parent.server.url(self)

    def asTranscodedImageURL(self, w, h, **extras):
        return self.parent.server.getImageTranscodeURL(self, w, h, **extras)


class PlexItemList(object):
    def __init__(self, data, item_cls, tag, server=None):
        self._data = data
        self._itemClass = item_cls
        self._itemTag = tag
        self._server = server
        self._items = None

    @property
    def items(self):
        if self._items is None:
            if self._data is not None:
                if self._server:
                    self._items = [self._itemClass(elem, server=self._server) for elem in self._data if elem.tag == self._itemTag]
                else:
                    self._items = [self._itemClass(elem) for elem in self._data if elem.tag == self._itemTag]
            else:
                self._items = []

        return self._items

    def __call__(self):
        return self.items

    def __len__(self):
        return len(self.items)

    def append(self, item):
        self.items.append(item)


class PlexMediaItemList(PlexItemList):
    def __init__(self, data, item_cls, tag, initpath=None, server=None, media=None):
        self._data = data
        self._itemClass = item_cls
        self._itemTag = tag
        self._initpath = initpath
        self._server = server
        self._media = media
        self._items = None

    @property
    def items(self):
        if self._items is None:
            if self._data is not None:
                self._items = [self._itemClass(elem, self._initpath, self._server, self._media) for elem in self._data if elem.tag == self._itemTag]
            else:
                self._items = []

        return self._items


class PlexObject(object):
    def __init__(self, data, initpath=None, server=None, container=None):
        self.initpath = initpath
        self.key = None
        self.server = server
        self.container = container
        self.mediaChoice = None
        self.titleSort = PlexValue('')

        if data is None:
            return

        self._setData(data)

        self.init(data)

    def _setData(self, data):
        for k, v in data.attrib.items():
            setattr(self, k, PlexValue(v, self))

    def __getattr__(self, attr):
        if not self.isFullObject():
            self.reload()
            if attr in self.__dict__:
                return self.__dict__[attr]

        a = PlexValue('', self)
        a.NA = True

        try:
            setattr(self, attr, a)
        except AttributeError:
            util.LOG('Failed to set attribute: {0} ({1})'.format(attr, self.__class__))

        return a

    def init(self, data):
        pass

    def isFullObject(self):
        return self.initpath is None or self.key is None or self.initpath == self.key

    @property
    def defaultThumb(self):
        return self.thumb

    def refresh(self):
        import requests
        self.server.query('%s/refresh' % self.key, method=requests.put)

    def reload(self):
        """ Reload the data for this object from PlexServer XML. """
        try:
            data = self.server.query(self.key)
        except Exception, e:
            util.ERROR(e)
            self.initpath = self.key
            return

        self.initpath = self.key
        self._setData(data[0])

    def _findLocation(self, data):
        elem = data.find('Location')
        if elem is not None:
            return elem.attrib.get('path')
        return None

    def _findPlayer(self, data):
        elem = data.find('Player')
        if elem is not None:
            from plexapi.client import Client
            return Client(self.server, elem)
        return None

    def _findTranscodeSession(self, data):
        elem = data.find('TranscodeSession')
        if elem is not None:
            from plexapi import media
            return media.TranscodeSession(self.server, elem)
        return None

    def _findUser(self, data):
        elem = data.find('User')
        if elem is not None:
            from plexapi.myplex import MyPlexUser
            return MyPlexUser(elem, self.initpath)
        return None

    def getAbsolutePath(self, attr):
        path = getattr(self, attr, None)
        if path is None:
            return None
        else:
            return self.container.getAbsolutePath(path)

    def getServer(self):
        return self.server

    def getTranscodeServer(self, localServerRequired=False, transcodeType=None):
        server = self.server

        # If the server is myPlex, try to use a different PMS for transcoding
        import myplexserver
        if server == myplexserver.MyPlexServer:
            fallbackServer = plexapp.SERVERMANAGER.getChannelServer()

            if fallbackServer:
                server = fallbackServer
            elif localServerRequired:
                return None

        return server


class PlexContainer(PlexObject):
    def __init__(self, data, initpath=None, server=None, address=None):
        PlexObject.__init__(self, data, initpath, server)
        self.setAddress(address)

    def __getitem__(self, idx):
        return self.resources[idx]

    def __iter__(self):
        for i in self.resources:
            yield i

    def __len__(self):
        return len(self.resources)

    def setAddress(self, address):
        if address != "/" and address.endswith("/"):
            self.address = address[:-1]
        else:
            self.address = address

        # TODO(schuyler): Do we need to make sure that we only hang onto the path here and not a full URL?
        if self.address.startswith("/") and "node.plexapp.com" not in self.address:
            util.FATAL("Container address is not an expected path: {0}".format(address))

    def getAbsolutePath(self, path):
        if path.startswith('/'):
            return path
        elif "://" in path:
            return path
        else:
            return self.address + "/" + path


class PlexServerContainer(PlexContainer):
    def __init__(self, data, initpath=None, server=None, address=None):
        PlexContainer.__init__(self, data, initpath, server, address)
        import plexserver
        self.resources = [plexserver.PlexServer(elem) for elem in data]


def findItem(server, path, title):
    for elem in server.query(path):
        if elem.attrib.get('title').lower() == title.lower():
            return buildItem(server, elem, path)
    raise exceptions.NotFound('Unable to find item: {0}'.format(title))


def buildItem(server, elem, initpath, bytag=False, container=None):
    libtype = elem.tag if bytag else elem.attrib.get('type')
    if libtype in LIBRARY_TYPES:
        cls = LIBRARY_TYPES[libtype]
        return cls(elem, initpath=initpath, server=server, container=container)
    raise exceptions.UnknownType('Unknown library type: {0}'.format(libtype))


def listItems(server, path, libtype=None, watched=None, bytag=False):
    items = []
    data = server.query(path)
    container = PlexContainer(data, path, server, '')
    for elem in data:
        if libtype and elem.attrib.get('type') != libtype:
            continue
        if watched is True and elem.attrib.get('viewCount', 0) == 0:
            continue
        if watched is False and elem.attrib.get('viewCount', 0) >= 1:
            continue
        try:
            items.append(buildItem(server, elem, path, bytag, container))
        except exceptions.UnknownType:
            pass
    return items


def searchType(libtype):
    searchtypesstrs = [str(k) for k in SEARCHTYPES.keys()]
    if libtype in SEARCHTYPES + searchtypesstrs:
        return libtype
    stype = SEARCHTYPES.get(libtype.lower())
    if not stype:
        raise exceptions.NotFound('Unknown libtype: %s' % libtype)
    return stype
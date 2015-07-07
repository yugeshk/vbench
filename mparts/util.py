import sys, os, errno, socket, select, threading

class Progress(object):
    """A context manager that prints out progress messages before and
    after an action."""

    def __init__(self, msg, done = "done"):
        self.__msg = msg
        self.__done = done

    def __enter__(self):
        print >> sys.stderr, "%s..." % self.__msg

    def __exit__(self, typ, value, traceback):
        if typ == None:
            print >> sys.stderr, "%s... %s" % (self.__msg, self.__done)
        else:
            print >> sys.stderr, "%s... FAILED (%s)" % (self.__msg, value)

# using CM's micro benchmark's snippets to support
# all different kinds of filesystem
HOWTO_MKFS = {
    "ext2":"-F",
    "ext3":"-F",
    "ext4":"-F",
    "xfs":"-f",
    "btrfs":"-f",
    "jfs":"-q",
    "reiserfs":"-q",
}

LOOPDEV = "/dev/loop2"
NVMEDEV = "/dev/nvme0n1p1"
DISK_SIZE = "32G"
CURRPATH = os.path.abspath(".")
TMPPATH = os.path.join(CURRPATH, ".tmp")
DISKPATH = os.path.join(TMPPATH, "disk.img")

def execCmd(cmd):
    import subprocess
    p = subprocess.Popen(cmd, shell = True,
            stdout = None, stderr = None)
    p.wait()
    return p

def attachLoopdev(loopdev, target):
    return execCmd("sudo losetup %s %s" %
            (loopdev, target)).returncode == 0

def detachLoopdev(loopdev = LOOPDEV):
    execCmd("sudo losetup -d %s" % (loopdev))

def createDisk(target):
    execCmd("dd if=/dev/zero of=%s bs=1G "
            "count=1024000" % (target))

def mount_tmpfs(mnt_path):
    p = execCmd("sudo mount -t tmpfs -o mode=0777,size="
            "%s none %s" % (DISK_SIZE, mnt_path))
    return p.returncode == 0

def umountDisk(target):
    execCmd("sudo umount %s" % target)

def initializeDisk(fsType, noCPUs):
    # in case, if it was killed
    deinitializeDisk(fsType)
    testPath = os.path.join("/tmp/mosbench", fsType)

    maybeMakedirs(TMPPATH)
    if not mount_tmpfs(TMPPATH):
        raise ValueError("unable to mount")
    createDisk(DISKPATH)
    if attachLoopdev(LOOPDEV, DISKPATH) is False:
        raise ValueError("unable to attach loopdevice")

    p = execCmd("sudo mkfs." + fsType
		+ " " + HOWTO_MKFS.get(fsType)
		+ " " + LOOPDEV)
    if p.returncode is not 0:
        raise ValueError("mkfs failed")

    execCmd("sudo mkdir -p %s" % (testPath))
    p = execCmd("sudo mount -t %s %s %s" %
            (fsType, LOOPDEV, testPath))
    if p.returncode is not 0:
        raise ValueError("mounting failed")
    # need to own the directory!
    execCmd("sudo chown %s:%s %s/ -R" %
	(os.getuid(), os.getgid(), testPath))
    # copying what mkmounts does
    for i in range(noCPUs+1):
	maybeMakedirs(os.path.join(testPath, str(i)))
    maybeMakedirs(os.path.join(testPath, "spool"))
    baseSpoolDir = os.path.join(testPath, "spool", "input")
    maybeMakedirs(baseSpoolDir)
    for i in range(0, 10):
	maybeMakedirs(os.path.join(baseSpoolDir, str(i)))
    for i in range(ord('a'), ord('z') + 1):
	maybeMakedirs(os.path.join(baseSpoolDir, chr(i)))
	maybeMakedirs(os.path.join(baseSpoolDir, chr(i).title()))

def deinitializeDisk(fsType):
    testPath = os.path.join("/tmp/mosbench", fsType)
    if os.path.exists(testPath):
	    umountDisk(testPath)
    detachLoopdev()
    if os.path.exists(testPath):
	deleteDirectory(testPath)
    if os.path.exists(TMPPATH):
        umountDisk(TMPPATH)
	deleteDirectory(TMPPATH)

def deleteDirectory(p):
    """ deleting a directory with everything """
    for root, dirs, files in os.walk(p, topdown = False):
	for name in files:
	    deleteFile(os.path.join(root, name))
	for name in dirs:
	    os.rmdir(os.path.join(root, name))

def deleteFile(p):
    """delete a file"""
    os.remove(p)

def maybeMakedirs(p):
    """Like os.makedirs, but it is not an error for the directory
    already to already exist."""

    try:
        os.makedirs(p)
    except EnvironmentError, e:
        if e.errno != errno.EEXIST:
            raise

def dictToCmdline(dct):
    args = []
    for k, v in dct.items():
        args.append("--%s=%s" % (k, v))
    return args

def relpath(path, start=os.path.curdir):
    """Return a relative version of a path.
    Lifted from Python 2.6 os.path."""

    if not path:
        raise ValueError("no path specified")

    start_list = os.path.abspath(start).split(os.path.sep)
    path_list = os.path.abspath(path).split(os.path.sep)

    # Work out how much of the filepath is shared by start and path.
    i = len(os.path.commonprefix([start_list, path_list]))

    rel_list = [os.path.pardir] * (len(start_list)-i) + path_list[i:]
    if not rel_list:
        return os.path.curdir
    return os.path.join(*rel_list)

def isLocalhost(host):
    # Listen on a random port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    s.listen(1)
    s.setblocking(False)

    # Try to connect to that port on host.  This will come from a
    # random port.  For some reason, we can't use a 0 timeout, so we
    # use a really small one.
    try:
        c = socket.create_connection((host, s.getsockname()[1]), 0.01)
    except socket.error:
        return False

    # We're probably good, but accept the connection and make sure it
    # came from the right port.
    try:
        (a, _) = s.accept()
    except socket.error:
        return False
    return a.getpeername()[1] == c.getsockname()[1]

class Async(threading.Thread):
    """Execute a function asynchronously.  The caller can use sync to
    wait on and retrieve the function's result.  Inspired by Cilk's
    spawn and Haskell's par."""

    def __init__(self, fn, *args, **kwargs):
        threading.Thread.__init__(self)
        self.__fn = fn
        self.__args = args
        self.__kwargs = kwargs
        self.__result = self.__exc = None
        if 'daemon' in kwargs:
            self.setDaemon(kwargs.pop('daemon'))
        if 'threadname' in kwargs:
            self.setName(kwargs.pop('threadname'))
        self.start()

    def run(self):
        try:
            self.__result = self.__fn(*self.__args, **self.__kwargs)
        except:
            self.__exc = sys.exc_info()

    def sync(self):
        """Wait for the function call to complete.  Returns the
        function result.  If the function terminated with an
        exception, re-raises that exception in this thread."""

        self.join()
        if self.__exc:
            raise self.__exc[0], self.__exc[1], self.__exc[2]
        return self.__result


from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.svgPathPen import main as svgMain
from fontTools.pens.statisticsPen import StatisticsPen
from fontTools.varLib.interpolatable import PerContourPen
from collections import Counter, defaultdict
from itertools import permutations
import numpy as np
from scipy.optimize import linear_sum_assignment
import math
from pprint import pprint
import sys

fontfile = sys.argv[1]
count = None
demoS = None
if len(sys.argv) > 2:
    arg = sys.argv[2]
    if arg.startswith("U+"):
        demoS = int(arg[2:], 16)
    else:
        count = int(arg)

font = TTFont(fontfile)
upem = font['head'].unitsPerEm
hhea = font['hhea']
ascent, descent = hhea.ascent, hhea.descent
cmap = font['cmap'].getBestCmap()

LBase = 0x1100
VBase = 0x1161
TBase = 0x11A7
LCount = 19
VCount = 21
TCount = 28
SBase = 0xAC00
NCount = VCount * TCount
SCount = count if count else LCount * NCount

def isCombiningL(u):
    return LBase <= u <= LBase+LCount-1
def isCombiningV(u):
    return VBase <= VBase+VCount-1
def isCombiningT(u):
    return TBase+1 <= u <= TBase+TCount-1
def isCombinedS(u):
    return SBase <= u <= SBase+SCount-1
def decomposeS(S):
    L = (S - SBase) // NCount + LBase
    Nindex = (S - SBase) % NCount
    V = Nindex // TCount + VBase
    Tindex = Nindex % TCount
    T = Tindex + TBase if Tindex else None
    return (L,V,T)

def contourStructure(contour):
    # Use second byte of the operation name (curveTo, closePath, etc),
    # as that's unique.
    return ''.join(op[0][1] for op in contour)

def outlineStructure(outline):
    return ''.join(contourStructure(contour) for contour in outline)

def outlineVector(outline):
    if not outline:
        return []
    assert(outline[0][0][0] == "moveTo")
    initPos = outline[0][0][1][0]
    vec = []
    for contour in outline:
        for op in contour:
            for x_y in op[1]:
                vec.append(x_y[0] - initPos[0])
                vec.append(x_y[1] - initPos[1])
    return vec

def reconstructRecordingPenValues(structure, vector):
    # We saved the second char of the operation name; with num args
    op_map = {
      "o": ("moveTo", 1),
      "i": ("lineTo", 1),
      "C": ("qCurveTo", 2),
      "u": ("curveTo", 3),
      "l": ("closePath", 0),
      "n": ("endPath", 0),
    }
    ret = []
    it = iter(vector)
    for mnem in structure:
        op, nArgs = op_map[mnem]
        args = []
        for n in range(nArgs):
            x = next(it)
            y = next(it)
            args.append((x,y))
        ret.append((op, tuple(args)))
    return ret

def contourDiff(c1, c2):
    vecs = []
    for c in (c1, c2):
        rPen = RecordingPen()
        rPen.value = c
        stats = StatisticsPen(glyphset=glyphset)
        rPen.replay(stats)
        size = abs(stats.area) ** 0.5 * 0.5
        vector = (
            int(size),
            int(stats.meanX),
            int(stats.meanY),
            int(stats.stddevX * 2),
            int(stats.stddevY * 2),
            int(stats.correlation * size),
        )
        vecs.append(np.array(vector))
    diff = vecs[0] - vecs[1]
    return np.dot(diff, diff)

def matchingCost(G, matching):
    return sum(G[i][j] for i, j in enumerate(matching))

def matchOutline(shape, ref):
    assert len(shape) == len(ref)
    if outlineStructure(shape) == outlineStructure(ref):
        return shape

    # Perform a weighted-matching of outlines between shape and ref.
    # If found a perfect-matching, that's our solution.
    G = []
    for c1 in ref:
        row = []
        G.append(row)
        for c2 in shape:
            if contourStructure(c1) != contourStructure(c2):
                row.append(1e10)
                continue
            row.append(contourDiff(c1, c2))
    rows, cols = linear_sum_assignment(G)
    assert (rows == list(range(len(rows)))).all()
    if matchingCost(G, cols) >= 1e10:
        return None

    # We have a matching. Reorder contours and return
    reordered = []
    for c in cols:
        reordered.append(shape[c])

    return reordered



if demoS:
    S = demoS
    L,V,T = decomposeS(S)
    svgMain([fontfile, chr(L)+chr(V)+(chr(T) if T else '')+chr(S)])
    sys.exit(0)


alternates = defaultdict(list)

for weight in (100, 1000):
    shapes = {}
    glyphset = font.getGlyphSet(location={'wght':weight})
    for u in list(range(LBase, LBase+LCount)) + \
             list(range(VBase, VBase+VCount)) + \
             list(range(TBase+1, TBase+TCount)) + \
             list(range(SBase, SBase+SCount)):
        pen = PerContourPen(RecordingPen)
        glyphset[cmap[u]].draw(pen)
        shapes[u] = [recPen.value for recPen in pen.value]

    for S in range(SBase, SBase+SCount):
        L,V,T = decomposeS(S)
        if T is None:
            continue # Only doing LVT for now

        Llen = len(shapes[L])
        Vlen = len(shapes[V])
        Tlen = len(shapes[T])
        Slen = len(shapes[S])
        if Llen + Vlen + Tlen != Slen:
            #print("U+%04X: Contour count mismatch; skipping" % S)
            continue

        composed = [len(contour) for contour in shapes[S]]
        decomposed = []
        for u in (L, V, T):
            shape = shapes[u]
            component = [len(contour) for contour in shape]
            #print(component)
            decomposed.extend(component)
        if composed != decomposed:
            #print(composed)
            #print(decomposed)
            pass

        Sshape = shapes[S]
        matched = False
        for order in permutations((L,V,T)):
            # Chop shape for S into L,V,T components and save to respective lists
            # Assumption, I know...
            len0 = len(shapes[order[0]])
            len1 = len(shapes[order[1]])
            len2 = len(shapes[order[2]])
            shape0 = Sshape[:len0]
            shape1 = Sshape[len0:len0+len1]
            shape2 = Sshape[len0+len1:]

            matchedOutline0 = matchOutline(shape0, shapes[order[0]])
            matchedOutline1 = matchOutline(shape1, shapes[order[1]])
            matchedOutline2 = matchOutline(shape2, shapes[order[2]])
            if matchedOutline0 and matchedOutline1 and matchedOutline2:
                alternates[order[0]].append(matchedOutline0)
                alternates[order[1]].append(matchedOutline1)
                alternates[order[2]].append(matchedOutline2)
                matched = True
                break

        print("U+%04X: matched " % S, matched)

for unicode,alts in sorted(alternates.items()):
    print("U+%04X: Structure matched %d." % (unicode, len(alts)))

    struct = outlineStructure(alts[0])
    samples = []
    for alt in alts:
        samples.append(outlineVector(alt))

    # Remove duplicate samples, keeping order
    new_samples = {}
    for sample in samples:
        sample = tuple(sample)
        if sample not in new_samples:
            new_samples[sample] = 1
    samples = list(new_samples.keys())

    mat = np.matrix(samples)
    u,s,v = np.linalg.svd(mat, full_matrices=False)

    # Find number of "masters" to keep
    first = s[0] # Largest singular value
    for k in range(len(s)):
        if s[k] < first / 100:
            break

    # Truncate rank to k
    u = u[:,:k]
    s = s[:k]
    v = v[:k,:]

    reconst = np.round(u * np.diag(s) * v)
    error = reconst - mat
    print("Num masters %d max error without rounding masters %d" % (k, np.max(error)))

    # Multiply extracted features by singular values and be done with those values.
    v = np.diag(s) * v
    del s

    # v contains the list of shape-like features discovered, one in each row, and
    # u contains the combination factors of those, one row per sample.

    # Normalize range of each "axis" to 0-1; This extracts default master, and deltas.
    defaultMaster = np.zeros(np.shape(v[0]))
    for j in range(k):
        minV = np.min(u[:,j])
        maxV = np.max(u[:,j])
        diff = maxV - minV
        assert diff > 1e-5

        u[:,j] -= minV
        u[:,j] /= diff

        defaultMaster += v[j,:] * minV
        v[j,:] *= diff

    defaultMaster = np.round(defaultMaster)
    deltas = np.round(v)

    # Reconstruct again, from defaultMaster+deltas
    reconst = defaultMaster + u * deltas
    error = reconst - mat
    print("Num masters %d max error with rounding masters %d" % (k, np.max(error)))

    defaultMasterPenValues = reconstructRecordingPenValues(struct, defaultMaster.tolist()[0])
    masters = [defaultMasterPenValues]
    for delta in deltas:
        values = reconstructRecordingPenValues(struct, (defaultMaster+delta).tolist()[0])
        masters.append(values)

    instances = []
    for scalars in u:
        instance = np.matrix(defaultMaster)
        for scalar,delta in zip(scalars.tolist()[0],deltas):
            instance += scalar * delta
        values = reconstructRecordingPenValues(struct, instance.tolist()[0])
        instances.append(values)

    SVGs = []
    for image in instances:
        rPen = RecordingPen()
        rPen.value = image
        pen = SVGPathPen(glyphset)
        rPen.replay(pen)
        commands = pen.getCommands()
        SVGs.append(commands)

    scale = .1
    with open("U+%04X.svg" % unicode, "w") as fd:

        cols = 16
        width = upem * (cols + 1)
        height = upem * (math.ceil(len(SVGs) / cols) + 1)

        print('<?xml version="1.0" encoding="UTF-8"?>', file=fd)
        print('<svg width="%d" height="%d" xmlns="http://www.w3.org/2000/svg">' % (width*scale, height*scale), file=fd)
        print('<rect width="100%" height="100%" fill="white"/>', file=fd)
        y = 0
        for i,commands in enumerate(SVGs):
            if i % cols == 0:
                y += upem
                x = upem
            s = '<g transform="translate(%d %d) scale(%g -%g)"><path d="%s"/></g>' % (x*scale, y*scale, scale, scale, commands)
            print(s, file=fd)
            x += upem
        print('</svg>', file=fd)

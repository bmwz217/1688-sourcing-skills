"""Scan 1688 detail images, record an auditable plan, and recycle selected files."""

from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import argparse
import base64
import ctypes as C
import json
import os
import re
import struct
import subprocess
import sys
import time
from uuid import UUID

LOCAL = Path(os.environ['LOCALAPPDATA'])
RUNTIME = LOCAL / 'Codex' / '1688-image-downloader' / 'ocr-py312'
if sys.version_info[:2] == (3,12) and RUNTIME.is_dir():
    sys.path.insert(0,str(RUNTIME))
for setting in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ[setting] = '1'

import numpy as np
from PIL import Image, ImageOps

RULES = ('店铺推荐','定制')
FLOW = re.compile(r'(定制|订制|定做|客制|OEM|ODM)\s*(服务|加工|合作|采购)?\s*流程|流程.{0,3}(定制|订制|定做|客制)',re.I)
NORMALIZE = str.maketrans({'鋪':'铺','舖':'铺','薦':'荐','製':'制','訂':'订','務':'务','購':'购'})
IMAGE_EXTENSIONS = {'.jpg','.jpeg','.png','.webp','.avif','.gif','.bmp','.tif','.tiff'}
DETAIL = '产品详情页'
TEMPLATE_FILE = Path(__file__).with_name('详情图定制流程模板.json')
AXIS = np.arange(32)
DCT = np.cos(np.pi/32*np.outer(AXIS+.5,AXIS))

def natural(name):
    return tuple((1,int(s)) if s.isdigit() else (0,s.casefold()) for s in re.split(r'(\d+)',name))

def save_json(path,data):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    temp.replace(path)

def safe_directory(path):
    return not path.is_symlink() and not path.is_junction() and path.resolve(strict=True)==path

def inventory(root,only=None):
    if root.name==DETAIL:
        assert safe_directory(root)
        if only is None:
            paths=[p for p in root.iterdir() if p.is_file()]
        else:
            assert all(isinstance(name,str) and Path(name).name==name for name in only)
            paths=[root/name for name in only]
        return {root:sorted((p for p in paths if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file() and not p.is_symlink()),key=lambda p:natural(p.name))}
    if only is not None:
        raise ValueError('指定单批文件时，根目录必须是「产品详情页」。')
    result={}
    for where,children,files in os.walk(root):
        folder=Path(where)
        children[:]=[name for name in children if not (folder/name).is_symlink() and not (folder/name).is_junction()]
        if folder.name!=DETAIL:
            continue
        assert safe_directory(folder) and folder.is_relative_to(root)
        result[folder]=sorted((folder/name for name in files if Path(name).suffix.lower() in IMAGE_EXTENSIONS and not (folder/name).is_symlink()),key=lambda p:natural(p.name))
        children.clear()
    return result

def rgb_signature(image):
    image=ImageOps.exif_transpose(image)
    gray=image.convert('L')
    thumb=np.asarray(gray.resize((32,32),Image.Resampling.LANCZOS))
    spectrum=(DCT.T@thumb@DCT)[:8,:8].ravel()[1:]
    phash=int.from_bytes(np.packbits(spectrum>np.median(spectrum)).tobytes(),'big')
    gradient=np.asarray(gray.resize((9,8),Image.Resampling.LANCZOS))
    dhash=int.from_bytes(np.packbits(gradient[:,1:]>gradient[:,:-1]).tobytes(),'big')
    return phash,dhash,thumb

def template_signature(path):
    content=path.read_bytes()
    with Image.open(BytesIO(content)) as raw:
        im=ImageOps.exif_transpose(raw)
        p,d,t=rgb_signature(im)
        return dict(sha=sha256(content).hexdigest(),size=im.size,phash=p,dhash=d,thumb=base64.b64encode(t.tobytes()).decode('ascii'))

def template_match(dim,p,d,thumb,templates):
    width,height=dim
    for template in templates:
        w,h=template['size']
        if abs((height*w)/(width*h)-1)>.11:
            continue
        if (p^template['phash']).bit_count()>10 or (d^template['dhash']).bit_count()>12:
            continue
        other=np.frombuffer(base64.b64decode(template['thumb']),dtype=np.uint8).reshape(32,32)
        if np.abs(thumb.astype(np.int16)-other).mean()<=15:
            return True
    return False

def load_rgb(path,size):
    with Image.open(path) as im:
        return np.asarray(ImageOps.exif_transpose(im).convert('RGB').resize(size,Image.Resampling.LANCZOS),dtype=np.int16)

def same_view(path,reference,dim,other_dim,hash_distance):
    if hash_distance>6 or abs((dim[0]*other_dim[1])/(other_dim[0]*dim[1])-1)>.0025:
        return False
    width=min(dim[0],other_dim[0],768)
    height=min(dim[1],other_dim[1],round(width*dim[1]/dim[0]))
    if height>1024:
        width,height=round(width*1024/height),1024
    if min(width,height)<80:
        return False
    diff=np.abs(load_rgb(path,(width,height))-load_rgb(reference,(width,height)))
    if diff.mean()>1.65 or (diff.max(axis=2)>24).mean()>.012:
        return False
    # Pixel-local comparison prevents the hash from equating different captions
    # or small product features that occupy little of the full image.
    for part in np.array_split(diff, (height+15)//16,axis=0):
        if max(piece.mean() for piece in np.array_split(part,(width+15)//16,axis=1))>12:
            return False
    return True

def scan_hashes(root,folders,templates):
    known_templates={x['sha'] for x in templates}
    def check_folder(paths):
        files={}
        byte_map={}
        pixel_map={}
        visual=[]
        duplicates={}
        matches={}
        problems={}
        for path in paths:
            name=str(path.relative_to(root))
            digest=None
            try:
                data=path.read_bytes()
                digest=sha256(data).hexdigest()
                files[name]=digest
                if digest in known_templates:
                    matches[name]='定制流程图'
                if digest in byte_map:
                    duplicates[name]=[byte_map[digest],'文件哈希相同']
                    continue
                byte_map[digest]=name
                with Image.open(BytesIO(data)) as raw:
                    if getattr(raw,'n_frames',1)>1:
                        continue
                    im=ImageOps.exif_transpose(raw)
                    alpha='A' in im.getbands() or 'transparency' in im.info
                    color=im.convert('RGBA' if alpha else 'RGB')
                    pixel=sha256(f'{color.mode}:{color.size}:'.encode()+color.tobytes()).hexdigest()
                    if pixel in pixel_map:
                        earlier=pixel_map[pixel]
                        duplicates[name]=[earlier,'像素哈希相同']
                        if earlier in matches:
                            matches[name]=matches[earlier]
                        continue
                    pixel_map[pixel]=name
                    ph,dh,gray=rgb_signature(color)
                    if templates and template_match(color.size,ph,dh,gray,templates):
                        matches[name]='定制流程图'
                    if alpha:
                        continue
                    small=np.asarray(color.resize((64,64),Image.Resampling.LANCZOS),dtype=np.int16)
                    for earlier,dim,p0,s0 in visual:
                        delta=(ph^p0).bit_count()
                        if delta<=6 and np.abs(small-s0).mean()<=2.5 and same_view(path,root/earlier,color.size,dim,delta):
                            duplicates[name]=[earlier,'感知哈希及画面一致']
                            break
                    if name not in duplicates:
                        visual.append((name,color.size,ph,small))
            except (OSError,ValueError) as error:
                problems[name]=str(error)
        return files,duplicates,matches,problems
    files,duplicates,matches,problems={},{},{},{}
    with ThreadPoolExecutor(max_workers=8 if len(folders)>100 else 2) as workers:
        for index,row in enumerate(workers.map(check_folder,folders.values()),1):
            for a,b in zip((files,duplicates,matches,problems),row):
                a.update(b)
            if index%250==0:
                print(f'已比对图片哈希：{index}/{len(folders)} 个文件夹。',flush=True)
    # Same bytes as any verified template are equally eligible in other folders.
    matched_digests={files[name] for name in matches}
    for name,digest in files.items():
        if digest in matched_digests:
            matches[name]='定制流程图'
    return dict(files=files,duplicates=duplicates,templates=matches,hash_errors=problems)

def refresh_template_matches(root,data,templates):
    """Reuse the integrity and duplicate scan when only the reference catalog changed."""
    by_digest={}
    for name,digest in data['files'].items():
        by_digest.setdefault(digest,name)
    ratios=[row['size'][1]/row['size'][0] for row in templates]
    known={row['sha'] for row in templates}
    def search(entry):
        digest,name=entry
        if digest in known:
            return digest
        with Image.open(root/name) as raw:
            if getattr(raw,'n_frames',1)>1:
                return None
            width,height=raw.size
            if raw.getexif().get(274) in {5,6,7,8}:
                width,height=height,width
            if not any(abs((height/width)/ratio-1)<=.11 for ratio in ratios):
                return None
            ph,dh,thumb=rgb_signature(raw)
            return digest if template_match((width,height),ph,dh,thumb,templates) else None
    if templates:
        with ThreadPoolExecutor(max_workers=6) as pool:
            found=set(pool.map(search,by_digest.items()))
    else:
        found=set()
    return {name:'定制流程图' for name,digest in data['files'].items() if digest in found}

OCR=None
CV=None
DLL=[]
def init_ocr():
    global OCR,CV
    # On Windows an existing Microsoft CRT may be bundled with WPS. Preload
    # these two signed-runtime library names if present so SxS can resolve them.
    wp=LOCAL/'Kingsoft'/'WPS Office'
    if os.name=='nt' and wp.is_dir():
        for folder in sorted(wp.glob('*/office6'),reverse=True):
            paths=[folder/name for name in ('msvcp140.dll','msvcp140_1.dll')]
            if all(p.is_file() for p in paths):
                try:
                    DLL.append(os.add_dll_directory(str(folder)))
                    DLL.extend(C.WinDLL(str(p)) for p in paths)
                except OSError:
                    pass
                break
    import cv2
    from rapidocr_onnxruntime import RapidOCR
    cv2.setNumThreads(1)
    CV=cv2
    OCR=RapidOCR(intra_op_num_threads=1,inter_op_num_threads=1,max_side_len=608,det_limit_type='max',text_score=.48)

def clean_text(value):
    return re.sub(r'[^\w\u3400-\u9fff]','',value.translate(NORMALIZE))

def lines_from(image):
    pixels=CV.cvtColor(np.asarray(image.convert('RGB')),CV.COLOR_RGB2BGR)
    results,_=OCR(pixels,use_cls=False)
    if not results:
        return []
    lines=[]
    for coordinates,text,score in results:
        box=np.asarray(coordinates)
        x0,y0=box.min(axis=0)
        x1,y1=box.max(axis=0)
        lines.append((clean_text(text),float(score),[int(x0),int(y0),int(x1),int(y1)]))
    # Text can be detected as adjacent fragments, particularly spaced headings.
    ordered=sorted(lines,key=lambda line:line[2][0])
    joined=[]
    for index,(text,score,box) in enumerate(ordered):
        if not text or len(text)>8:
            continue
        x0,y0,x1,y1=box
        for next_text,next_score,next_box in ordered[index+1:]:
            a,b,c,d=next_box
            h=max(y1-y0,d-b,1)
            if a-x1>h:
                break
            if a>=x1-h*.2 and abs((y0+y1)-(b+d))<.7*h:
                pair=text+next_text
                if any(word in pair for word in RULES) or FLOW.search(pair):
                    joined.append((pair,min(score,next_score),[x0,min(y0,b),c,max(y1,d)]))
                break
    return lines+joined

def analyze(task):
    digest,path=task
    found={}
    flows=[]
    try:
        with Image.open(path) as source:
            for frame in range(getattr(source,'n_frames',1)):
                source.seek(frame)
                image=ImageOps.exif_transpose(source).convert('RGB')
                w,h=image.size
                step=max(160,int(w*1.05))
                size=max(210,int(w*1.35))
                starts=list(range(0,h,step)) if h>1.65*w else [0]
                if len(starts)>1 and starts[-1]+size>h and h-starts[-1]<size//4:
                    starts[-1]=max(0,h-size)
                for top in sorted(set(starts)):
                    tile=image.crop((0,top,w,min(top+size,h))) if len(starts)>1 else image
                    for text,score,box in lines_from(tile):
                        coordinates=[box[0],box[1]+top,box[2],box[3]+top]
                        hit=dict(text=text,score=round(score,3),box=coordinates,frame=frame)
                        for rule in RULES:
                            if rule in text and (rule not in found or score>found[rule]['score']):
                                found[rule]=hit
                        if FLOW.search(text):
                            flows.append(hit)
        return dict(sha=digest,hit=found,flow=sorted(flows,key=lambda x:-x['score'])[:8])
    except Exception as error:
        return dict(sha=digest,error=f'{type(error).__name__}: {error}')

def scan_ocr(root,files,state,workers):
    unique={}
    for name,digest in files.items():
        unique.setdefault(digest,str(root/name))
    found={}
    if state.is_file():
        for line in state.read_text(encoding='utf-8').splitlines():
            try:
                row=json.loads(line)
            except json.JSONDecodeError:
                continue
            if row['sha'] in unique and not row.get('error'):
                found[row['sha']]=row
    queue=[(key,value) for key,value in unique.items() if key not in found]
    print(f'OCR 开始：已识别 {len(found)} 张，待识别 {len(queue)} 张不同图片。',flush=True)
    with state.open('a',encoding='utf-8',buffering=1) as stream:
        def save(results):
            for row in results:
                found[row['sha']]=row
                stream.write(json.dumps(row,ensure_ascii=False)+'\n')
                if len(found)%250==0:
                    print(f'OCR {len(found)}/{len(unique)}',flush=True)
        if queue and len(queue)<=100:
            init_ocr()
            save(map(analyze,queue))
        elif queue:
            with ProcessPoolExecutor(max_workers=workers,initializer=init_ocr) as pool:
                save(pool.map(analyze,queue,chunksize=4))
    assert found.keys()==unique.keys()
    return found

def plan_matches(root,data,ocr):
    invalid={}
    for name,digest in data['files'].items():
        result=ocr.get(digest,{})
        labels=list(result.get('hit',{}))
        if name in data['templates']:
            labels.insert(0,'定制流程图')
        if labels:
            invalid[name]={'labels':labels}
    bad_keepers={str((root/name).parent) for name,(kept,_) in data['duplicates'].items()
                 if name not in invalid and kept in invalid}
    duplicates=data['duplicates']
    if bad_keepers:
        # Search surviving images again if a duplicate group originally pointed
        # to an invalid image. No near-match links are inferred transitively.
        folders=defaultdict(list)
        for name in data['files']:
            path=root/name
            if str(path.parent) in bad_keepers and name not in invalid:
                folders[path.parent].append(path)
        rechecked=scan_hashes(root,folders,[])
        if rechecked['hash_errors']:
            raise RuntimeError('重选同目录重复图的保留项时无法读取图片。')
        duplicates={name:value for name,value in duplicates.items() if str((root/name).parent) not in bad_keepers}
        duplicates.update(rechecked['duplicates'])
    for name,(kept,reason) in duplicates.items():
        if name not in invalid and kept not in invalid:
            invalid[name]={'labels':['同目录重复图'],'keep':kept,'match':reason}
    return invalid

class GUID(C.Structure):
    _fields_=[('d1',C.c_uint32),('d2',C.c_ushort),('d3',C.c_ushort),('d4',C.c_ubyte*8)]

def guid(value):
    return GUID.from_buffer_copy(UUID(value).bytes_le)

def checked(hr,action):
    if hr<0:
        raise OSError(f'{action}: 0x{hr&0xffffffff:08x}')

def method(obj,index,result,*args):
    vtable=C.cast(obj,C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(result,C.c_void_p,*args)(vtable[index])

def native_recycle(paths):
    ole=C.WinDLL('ole32')
    shell=C.WinDLL('shell32')
    ole.CoInitializeEx.argtypes=(C.c_void_p,C.c_uint32)
    ole.CoInitializeEx.restype=C.c_long
    ole.CoCreateInstance.argtypes=(C.POINTER(GUID),C.c_void_p,C.c_uint32,C.POINTER(GUID),C.POINTER(C.c_void_p))
    ole.CoCreateInstance.restype=C.c_long
    shell.SHCreateItemFromParsingName.argtypes=(C.c_wchar_p,C.c_void_p,C.POINTER(GUID),C.POINTER(C.c_void_p))
    shell.SHCreateItemFromParsingName.restype=C.c_long
    cls=guid('3ad05575-8857-4850-9277-11b85bdb8e09')
    api=guid('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')
    item_api=guid('43826d1e-e718-42ee-bc55-a1e261c37bfe')
    checked(ole.CoInitializeEx(None,2),'CoInitializeEx')
    task=C.c_void_p()
    items=[]
    try:
        checked(ole.CoCreateInstance(C.byref(cls),None,1,C.byref(api),C.byref(task)),'CoCreateInstance')
        recycle_only=0x00080000
        flags=recycle_only|0x20000000|0x00100000|0x0400|0x0004
        checked(method(task,5,C.c_long,C.c_uint32)(task,flags),'SetOperationFlags')
        for path in paths:
            item=C.c_void_p()
            checked(shell.SHCreateItemFromParsingName(str(path),None,C.byref(item_api),C.byref(item)),'SHCreateItemFromParsingName')
            items.append(item)
            checked(method(task,18,C.c_long,C.c_void_p,C.c_void_p)(task,item,None),'DeleteItem')
        checked(method(task,21,C.c_long)(task),'PerformOperations')
        aborted=C.c_int()
        checked(method(task,22,C.c_long,C.POINTER(C.c_int))(task,C.byref(aborted)),'GetAnyOperationsAborted')
        if aborted.value:
            raise RuntimeError('Windows 停止了回收操作。')
    finally:
        if task.value:
            method(task,2,C.c_uint32)(task)
        for item in items:
            method(item,2,C.c_uint32)(item)
        ole.CoUninitialize()

def previous_path(meta):
    try:
        blob=meta.read_bytes()
        offset={1:24,2:28}.get(struct.unpack_from('<Q',blob)[0])
        return Path(blob[offset:].decode('utf-16-le').split('\x00',1)[0]) if offset else None
    except (OSError,ValueError,struct.error):
        return None

def confirm_recycle(root,files,selection,recovered_log=None):
    if not selection:
        print('详情图清理完成：没有命中图片。',flush=True)
        return
    if os.name!='nt':
        raise OSError('批量清理需要 Windows 回收站。')
    targets={root/name:files[name] for name in selection}
    for name,info in selection.items():
        path=root/name
        if not (path.is_relative_to(root) and safe_directory(path.parent) and path.parent.name==DETAIL and not path.is_symlink()):
            raise ValueError(f'清理范围之外的路径：{path}')
        if path.exists():
            if not (path.is_file() and path.resolve(strict=True)==path and sha256(path.read_bytes()).hexdigest()==targets[path]):
                raise ValueError(f'源图片已变更：{path}')
        if info.get('keep'):
            keep=root/info['keep']
            if not (keep.parent==path.parent and keep not in targets and keep.is_file() and not keep.is_symlink()):
                raise ValueError(f'重复组没有有效的保留项：{path}')
            if sha256(keep.read_bytes()).hexdigest()!=files[info['keep']]:
                raise ValueError(f'保留图片已变更：{keep}')
    sid=re.search(rb'S-1-[0-9-]+',subprocess.check_output(['whoami','/user','/fo','csv','/nh'])).group().decode('ascii')
    bin_dir=Path(root.anchor)/'$Recycle.Bin'/sid
    seen=set(bin_dir.glob('$I*'))
    pending_meta=set(seen)
    def find(needed):
        found={}
        for meta in pending_meta.copy():
            origin=previous_path(meta)
            if origin is None:
                continue
            if origin not in needed:
                pending_meta.discard(meta)
                continue
            payload=meta.with_name('$R'+meta.name[2:])
            if payload.is_file() and sha256(payload.read_bytes()).hexdigest()==targets[origin]:
                found[origin]=payload
                pending_meta.discard(meta)
        return found
    prior={p for p in targets if not p.exists()}
    verified=find(prior)
    assert verified.keys()==prior,'有源文件缺失，且无法在回收站核对。'
    remaining=[p for p in targets if p not in prior]
    for start in range(0,len(remaining),80):
        batch=remaining[start:start+80]
        native_recycle(batch)
        waiting=set(batch)
        for _ in range(20):
            observed=set(bin_dir.glob('$I*'))
            pending_meta.update(observed-seen)
            seen.update(observed)
            available=find(waiting)
            verified.update(available)
            waiting.difference_update(available)
            if not waiting:
                break
            time.sleep(.25)
        assert not waiting and not any(p.exists() for p in batch),'部分图片未能确认进入回收站。'
        print(f'已确认进入回收站：{len(verified)}/{len(targets)} 张。',flush=True)
    assert verified.keys()==targets.keys() and all(p.is_file() for p in verified.values())
    if recovered_log:
        save_json(recovered_log,{str(a):str(b) for a,b in verified.items()})
    labels=Counter(label for e in selection.values() for label in e['labels'])
    print('详情图清理完成：'+json.dumps(dict(移入回收站=len(verified),各规则=labels),ensure_ascii=False),flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path,nargs='?')
    parser.add_argument('--report',type=Path)
    parser.add_argument('--only-files',type=Path)
    parser.add_argument('--workers',type=int,default=8)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--apply-report',type=Path)
    parser.add_argument('--template',type=Path,default=TEMPLATE_FILE)
    parser.add_argument('--teach-flow',type=Path,nargs='+')
    args=parser.parse_args()
    if args.teach_flow:
        existing=json.loads(args.template.read_text(encoding='utf-8')) if args.template.is_file() else []
        catalog={entry['sha']:entry for entry in existing}
        for image in args.teach_flow:
            entry=template_signature(image)
            catalog[entry['sha']]=entry
        save_json(args.template,list(catalog.values()))
        print(f'已保存 {len(catalog)} 个人工核对的定制流程图哈希模板。',flush=True)
        return
    if args.root is None:
        parser.error('请指定详情图根目录。')
    raw=args.root.absolute()
    root=raw.resolve(strict=True)
    if raw!=root or not safe_directory(root):
        raise ValueError('不允许对链接或非规范路径批量清理。')
    if args.apply_report:
        report=json.loads(args.apply_report.read_text(encoding='utf-8'))
        assert Path(report['root'])==root and not report['errors']
        confirm_recycle(root,report['files'],report['moves'],args.apply_report.with_suffix('.recycled.json'))
        return
    if not args.report:
        raise ValueError('请指定 --report 输出扫描结果。')
    only=json.loads(args.only_files.read_text(encoding='utf-8')) if args.only_files else None
    folders=inventory(root,only)
    catalog_bytes=args.template.read_bytes() if args.template.is_file() else b'[]'
    templates=json.loads(catalog_bytes)
    template_digest=sha256(catalog_bytes).hexdigest() if templates else None
    folder_count=sum(len(v) for v in folders.values())
    print(f'扫描 {len(folders)} 个「产品详情页」文件夹、{folder_count} 张图片。',flush=True)
    hash_log=args.report.with_suffix('.hash.json')
    saved_hashes=json.loads(hash_log.read_text(encoding='utf-8')) if hash_log.is_file() else None
    if saved_hashes:
        if saved_hashes['root']!=str(root):
            raise ValueError('该扫描报告属于其他目录。')
        now={str(p.relative_to(root)) for paths in folders.values() for p in paths}
        if set(saved_hashes['files'])!=now:
            print('详情图路径有变化，重新核对哈希并按内容复用已完成的 OCR。',flush=True)
            saved_hashes=None
    if saved_hashes:
        data=saved_hashes
        if data.get('template_digest')!=template_digest:
            print('使用已核对的定制流程模板重新比对图片哈希。',flush=True)
            data['templates']=refresh_template_matches(root,data,templates)
            data['template_digest']=template_digest
            save_json(hash_log,data)
    else:
        data=scan_hashes(root,folders,templates)
        data['root']=str(root)
        data['template_digest']=template_digest
        save_json(hash_log,data)
    ocr=scan_ocr(root,data['files'],args.report.with_suffix('.ocr.jsonl'),args.workers)
    errors={**data['hash_errors'],**{key:value['error'] for key,value in ocr.items() if value.get('error')}}
    moves=plan_matches(root,data,ocr)
    report=dict(root=str(root),folders=len(folders),files=data['files'],moves=moves,
                flow_candidates={key:result['flow'] for key,result in ocr.items() if result.get('flow')},
                errors=errors)
    save_json(args.report,report)
    print(json.dumps(dict(扫描=folder_count,匹配=len(moves),规则=Counter(label for row in moves.values() for label in row['labels']),流程候选=len(report['flow_candidates']),未识别=len(errors)),ensure_ascii=False),flush=True)
    if args.apply:
        if errors:
            raise RuntimeError('存在无法确认的图像，报告已保留，本轮未移动图片。')
        confirm_recycle(root,data['files'],moves,args.report.with_suffix('.recycled.json'))

if __name__=='__main__':
    main()

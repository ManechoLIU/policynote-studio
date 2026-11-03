"""Local, editable insurance infographic renderer. No network or model calls."""
import json, math, random, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
INK = '#292D2A'
COLORS = ['#8ABACC', '#AFC684', '#E2B16F', '#CE8F86']
FONTS = Path('C:/Windows/Fonts')

def font(size, bold=False):
    return ImageFont.truetype(str(FONTS / ('msyhbd.ttc' if bold else 'simkai.ttf')), size)

def txt(d, xy, s, size=39, bold=False, fill=INK):
    d.text(xy, s, font=font(size, bold), fill=fill)

def line(d, pts, fill=INK, width=3):
    d.line(pts, fill=fill, width=width, joint='curve')

def sketch_box(d, box, color, rng):
    x,y,x2,y2 = box
    d.rounded_rectangle((x+7,y+8,x2+7,y2+8),radius=13,fill=color)
    d.rounded_rectangle(box,radius=13,fill='#FFFDF8')
    for delta in [0,4]:
        pts=[(x+delta+rng.uniform(-2,2),y+delta),(x2-delta,y+rng.uniform(-2,2)+delta),
             (x2+rng.uniform(-2,2)-delta,y2-delta),(x+delta,y2+rng.uniform(-2,2)-delta),(x+delta,y+delta)]
        line(d,pts,INK,2)
    for xx in range(int(x+18),int(x2-10),18):
        line(d,[(xx,y2-9),(min(xx+10,x2-8),y2-9)],color,3)

def icon(d, x, y, kind, color, scale=1):
    def L(points,w=3): line(d,[(x+a*scale,y+b*scale) for a,b in points],INK,w)
    def E(box,fill=None): d.ellipse(tuple(v for p in [(x+box[0]*scale,y+box[1]*scale),(x+box[2]*scale,y+box[3]*scale)] for v in p),outline=INK,width=3,fill=fill)
    def person(px,py):
        E((px,py,px+22,py+22)); L([(px+11,py+22),(px+11,py+61)]); L([(px-5,py+38),(px+11,py+28),(px+27,py+38)]); L([(px+11,py+61),(px-1,py+80)]); L([(px+11,py+61),(px+23,py+80)])
    if kind=='family': person(2,5); person(51,5); person(28,37)
    elif kind=='hospital':
        L([(8,105),(8,27),(83,27),(83,105),(8,105)])
        d.rectangle((x+32*scale,y+3*scale,x+58*scale,y+51*scale),fill=color)
        d.rectangle((x+21*scale,y+15*scale,x+69*scale,y+39*scale),fill=color)
        for a in [22,55]: L([(a,65),(a+13,65),(a+13,80),(a,80),(a,65)])
    elif kind=='wallet':
        L([(6,35),(85,35),(85,96),(6,96),(6,35),(65,12),(76,35)])
        L([(62,56),(94,56),(94,78),(62,78),(62,56)]); E((71,63,78,70),color)
        E((5,0,36,31),color); txt(d,(x+12*scale,y), '¥',int(24*scale),True)
    elif kind=='home':
        L([(0,46),(46,4),(94,46)]); L([(13,39),(13,104),(80,104),(80,39)])
        L([(37,104),(37,66),(58,66),(58,104)])
        E((60,0,90,30),color)
    else:
        E((2,3,94,95)); L([(48,20),(48,49),(70,61)]); L([(12,100),(0,112),(28,112)])

def validate(data):
    assert 1 <= len(data['pages']) <= 9, 'Expected 1–9 pages'
    for p in data['pages']:
        assert 1<=len(p['title'])<=2 and all(len(s)<=11 for s in p['title']), 'Title too long'
        assert len(p.get('subtitle',''))<=24, 'Subtitle too long'
        assert len(p.get('takeaway',''))<=25, 'Takeaway too long'
        assert len(p['panels']) == (4 if p['layout']=='overview' else 3), 'Wrong panel count'
        for b in p['panels']:
            assert len(b['heading'])<=9 and 1<=len(b['lines'])<=3, 'Panel too dense'
            limit = 13 if p['layout']=='overview' else 20
            assert all(len(s)<=limit for s in b['lines']), 'Panel line too long'

def render(data,out):
    validate(data); out.mkdir(parents=True,exist_ok=True); reports=[]
    for i,p in enumerate(data['pages']):
        rng=random.Random(200+i)
        im=Image.new('RGB',(1080,1440),'#FFFEFA'); d=ImageDraw.Draw(im)
        # Faint paper grain, seeded for reproducibility.
        for _ in range(17000):
            x=rng.randrange(1080); y=rng.randrange(1440); d.point((x,y),fill=rng.choice(['#F6F4EE','#F4F1E9','#F9F7F1']))
        txt(d,(61,36),data.get('series','家庭保障笔记'),27)
        txt(d,(922,36),f'{i+1:02d}/{len(data["pages"]):02d}',27)
        for j,s in enumerate(p['title']):
            size=77 if len(s)>9 else 86
            if j==len(p['title'])-1:
                line(d,[(60,132+j*113),(min(1010,65+len(s)*size),125+j*113)],COLORS[i%4],24)
            txt(d,(58,88+j*113),s,size,True)
        txt(d,(62,328),p.get('subtitle',''),35)
        if p['layout']=='overview':
            boxes=[(58,420,516,794),(561,420,1019,794),(58,839,516,1213),(561,839,1019,1213)]
            for k,(b,box) in enumerate(zip(p['panels'],boxes)):
                sketch_box(d,box,COLORS[k],rng); x,y,_,_=box
                txt(d,(x+24,y+24),b['heading'],43,True)
                icon(d,x+173,y+102,b.get('icon','family'),COLORS[k],1)
                for row,s in enumerate(b['lines']): txt(d,(x+23,y+239+row*43),s,31)
        else:
            for k,b in enumerate(p['panels']):
                box=(58,418+k*268,1014,653+k*268); sketch_box(d,box,COLORS[k],rng)
                x,y,_,_=box; icon(d,x+31,y+75,b.get('icon','family'),COLORS[k],1.02)
                txt(d,(x+169,y+17),b['heading'],47,True)
                line(d,[(x+169,y+73),(x+169+len(b['heading'])*47,y+69)],COLORS[k],9)
                for row,s in enumerate(b['lines']): txt(d,(x+169,y+85+row*45),s,36)
        txt(d,(62,1262),p.get('takeaway',''),35,True)
        line(d,[(60,1320),(1018,1320)],'#BBB9AE',2)
        txt(d,(62,1340),'教学模拟 · 非作者原稿 · 待专业与公司审核',25)
        path=out/f'{i+1:02d}.png'; im.save(path)
        reports.append({'file':path.name,'size':[1080,1440],'bytes':path.stat().st_size})
    cols=3; thumb_w=270; thumb_h=360
    rows=math.ceil(len(data['pages'])/cols)
    sheet=Image.new('RGB',(cols*(thumb_w+24)+24,rows*(thumb_h+45)+30),'#EAE9E3'); sd=ImageDraw.Draw(sheet)
    for i in range(len(data['pages'])):
        x=24+(i%cols)*(thumb_w+24); y=20+(i//cols)*(thumb_h+45)
        thumb=Image.open(out/f'{i+1:02d}.png').resize((thumb_w,thumb_h),Image.Resampling.LANCZOS)
        sheet.paste(thumb,(x,y)); txt(sd,(x,y+thumb_h+3),f'第{i+1}页',23)
    sheet.save(out/'整套预览.png')
    (out/'audit.json').write_text(json.dumps({'renderer':'insurance-handdrawn-v1','checks':'schema, dimensions, nonempty; requires visual/content review','pages':reports},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pages':len(reports),'output':str(out)},ensure_ascii=False))

if __name__=='__main__':
    source=Path(sys.argv[1]).resolve(); out=Path(sys.argv[2]).resolve()
    for path in [source,out]: path.relative_to(ROOT/'outputs')
    render(json.loads(source.read_text(encoding='utf-8-sig')),out)

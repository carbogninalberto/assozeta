"""Decode the API's monochrome SVG QR through an independent ZXing reader.

Only the horizontal-stroke SVG subset emitted by the API is admitted. Pillow and
ZXing are test dependencies; no application or backend implementation is imported.
"""
import re
from urllib.parse import unquote_to_bytes
from xml.etree import ElementTree


def rasterize_svg_qr(uri):
    from PIL import Image,ImageDraw
    prefix='data:image/svg+xml;charset=utf-8,'
    assert uri.startswith(prefix),'QR is not an inline UTF-8 SVG'
    xml=unquote_to_bytes(uri[len(prefix):])
    assert len(xml)<500000 and b'<!' not in xml,'unexpected SVG declaration'
    root=ElementTree.fromstring(xml)
    assert root.tag=='{http://www.w3.org/2000/svg}svg'
    size=int(root.attrib['width'])
    assert str(size)==root.attrib['height'] and 29<=size<=185 and (size-29)%4==0,'QR symbol bounds'
    assert set(root.attrib)<= {'width','height','class'} and root.attrib.get('class')=='segno'
    image=Image.new('L',(size,size),255);draw=ImageDraw.Draw(image)
    assert len(root)==1,'unexpected SVG children'
    path=root[0]
    assert path.tag=='{http://www.w3.org/2000/svg}path' and set(path.attrib)=={'class','stroke','d'}
    assert path.attrib['class']=='qrline' and path.attrib['stroke']=='#000'
    data=path.attrib['d'];tokens=re.findall(r'[Mmh]|-?\d+(?:\.\d+)?',data)
    assert re.sub(r'[Mmh]|-?\d+(?:\.\d+)?|[ ,\t\n\r]','',data)==''
    x=y=0;index=0
    while index<len(tokens):
        op=tokens[index];index+=1
        if op in ('M','m'):
            a,b=map(float,tokens[index:index+2]);index+=2
            x,y=(a,b) if op=='M' else (x+a,y+b)
        else:
            assert op=='h'
            length=float(tokens[index]);index+=1
            assert x.is_integer() and length.is_integer() and y%1==.5 and length>0
            assert 4<=x<x+length<=size-4 and 4<=int(y)<size-4,'QR quiet zone overwritten'
            draw.line((int(x),int(y),int(x+length)-1,int(y)),fill=0)
            x+=length
    return image


def decode_svg_qr(uri):
    from PIL import Image
    import zxingcpp
    image=rasterize_svg_qr(uri)
    size=image.width
    decoded=zxingcpp.read_barcodes(image.resize((size*6,size*6),Image.Resampling.NEAREST),formats=zxingcpp.BarcodeFormat.QRCode,is_pure=True)
    assert len(decoded)==1 and decoded[0].format==zxingcpp.BarcodeFormat.QRCode,'SVG QR is not decodable'
    return decoded[0].text,image

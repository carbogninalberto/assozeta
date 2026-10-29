package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"math"
)

// This order is Pillow 11.1's default preinit followed by init. Signatures for
// missing codecs are retained as explicit gaps, including potential no-accept
// text/scientific formats. These broad guards can reject ambiguous malformed
// inputs; they never silently report them as completed image parity.
func newImageFormats(path string, l ProcessorLimits) ([]imageFormat, error) {
	supported := make(map[string]imageFormat, 18)
	bmp, err := NewBMPFileProcessor(path, BMPFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["BMP"] = imageFormat{name: "BMP", accepts: bmpSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadBMPHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := bmp.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	gif, err := NewGIFFileProcessor(path, GIFFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["GIF"] = imageFormat{name: "GIF", accepts: gifSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadGIFHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := gif.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	jpeg, err := NewJPEGFileProcessor(path, JPEGFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["JPEG"] = imageFormat{name: "JPEG", accepts: func(b []byte) bool { return bytes.HasPrefix(b, []byte{255, 216, 255}) }, probe: func(ctx context.Context, b []byte) error { _, err := InspectJPEG(ctx, b, l.MaxHeaderBytes); return err }, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := jpeg.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	ppm, err := NewPPMFileProcessor(path, PPMFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["PPM"] = imageFormat{name: "PPM", accepts: ppmSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadPPMHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := ppm.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	png, err := NewPNGFileProcessor(path, PNGFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxTailBytes: l.MaxTailBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["PNG"] = imageFormat{name: "PNG", accepts: func(b []byte) bool { return bytes.HasPrefix(b, []byte("\x89PNG\r\n\x1a\n")) }, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadPNGHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := png.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	pcx, err := NewPCXFileProcessor(path, PCXFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["PCX"] = imageFormat{name: "PCX", accepts: pcxSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadPCXHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := pcx.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	gbr, err := NewGBRFileProcessor(path, GBRFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["GBR"] = imageFormat{name: "GBR", rejected: ErrGBRHeader, terminal: ErrGBRPixelLimit, accepts: gbrSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadGBRHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := gbr.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	msp, err := NewMSPFileProcessor(path, MSPFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["MSP"] = imageFormat{name: "MSP", accepts: mspSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadMSPHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := msp.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	qoi, err := NewQOIFileProcessor(path, QOIFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["QOI"] = imageFormat{name: "QOI", accepts: qoiSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadQOIHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := qoi.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	sgi, err := NewSGIFileProcessor(path, SGIFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["SGI"] = imageFormat{name: "SGI", accepts: sgiSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadSGIHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := sgi.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	sun, err := NewSUNFileProcessor(path, SUNFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["SUN"] = imageFormat{name: "SUN", accepts: sunSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadSUNHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := sun.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	tga, err := NewTGAFileProcessor(path, TGAFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["TGA"] = imageFormat{name: "TGA", accepts: tgaSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadTGAHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := tga.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	xbm, err := NewXBMFileProcessor(path, XBMFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["XBM"] = imageFormat{name: "XBM", accepts: xbmSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadXBMHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := xbm.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	ico, err := NewICOFileProcessor(path, ICOFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxTailBytes: l.MaxTailBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["ICO"] = imageFormat{name: "ICO", accepts: icoSignature, probe: func(ctx context.Context, b []byte) error { _, err := ico.decode(ctx, b); return err }, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := ico.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	webp, err := NewWebPFileProcessor(path, WebPFileLimits{Codec: l.Codec, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["WEBP"] = imageFormat{name: "WEBP", accepts: webpSignature, probe: func(ctx context.Context, b []byte) error { _, err := webp.decoder.Inspect(ctx, b); return err }, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := webp.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	cur, err := NewCURFileProcessor(path, CURFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["CUR"] = imageFormat{name: "CUR", rejected: ErrCURRejected, terminal: ErrCURTerminal, accepts: curSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadCURHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := cur.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	pcd, err := NewPCDFileProcessor(path, PCDFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["PCD"] = imageFormat{name: "PCD", rejected: ErrPCDHeader, accepts: pcdSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadPCDHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := pcd.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	pixar, err := NewPIXARFileProcessor(path, PIXARFileLimits{Codec: l.Codec, MaxHeaderBytes: l.MaxHeaderBytes, MaxDecodeBytes: l.MaxDecodeBytes, MaxTransformBytes: l.MaxTransformBytes})
	if err != nil {
		return nil, err
	}
	supported["PIXAR"] = imageFormat{name: "PIXAR", rejected: ErrPIXARHeader, terminal: ErrPIXARPixelLimit, accepts: pixarSignature, probe: func(ctx context.Context, b []byte) error {
		_, err := ReadPIXARHeader(ctx, b, l.MaxHeaderBytes)
		return err
	}, process: func(ctx context.Context, b []byte, maxSize int) (imageFileResult, error) {
		v, err := pixar.processSize(ctx, b, maxSize)
		return imageFileResult(v), err
	}}
	order := []string{"BMP", "GIF", "JPEG", "PPM", "PNG", "AVIF", "BLP", "BUFR", "CUR", "PCX", "DDS", "EPS", "FITS", "FLI", "FTEX", "GBR", "GRIB", "HDF5", "JPEG2000", "ICNS", "ICO", "IM", "IMT", "IPTC", "MCIDAS", "MPEG", "TIFF", "MSP", "PCD", "PIXAR", "PSD", "QOI", "SGI", "SPIDER", "SUN", "TGA", "WEBP", "WMF", "XBM", "XPM", "XVTHUMB"}
	formats := make([]imageFormat, 0, len(order))
	for _, name := range order {
		if f, ok := supported[name]; ok {
			formats = append(formats, f)
		} else {
			formats = append(formats, imageFormat{name: name, accepts: func(b []byte) bool { return missingImageCandidate(name, b) }})
		}
	}
	return formats, nil
}

func missingImageCandidate(name string, b []byte) bool {
	prefix := func(s string) bool { return bytes.HasPrefix(b, []byte(s)) }
	switch name {
	case "AVIF":
		if len(b) < 12 || string(b[4:8]) != "ftyp" {
			return false
		}
		switch string(b[8:12]) {
		case "avif", "avis", "mif1", "msf1":
			return true
		}
		return false
	case "BLP":
		return prefix("BLP1") || prefix("BLP2")
	case "BUFR":
		return prefix("BUFR") || prefix("ZCZC")
	case "DDS":
		return prefix("DDS ")
	case "EPS":
		return prefix("%!PS") || prefix("\xc5\xd0\xd3\xc6")
	case "FITS":
		return prefix("SIMPLE")
	case "FLI":
		return len(b) >= 16 && (binary.LittleEndian.Uint16(b[4:]) == 0xaf11 || binary.LittleEndian.Uint16(b[4:]) == 0xaf12) && (binary.LittleEndian.Uint16(b[14:]) == 0 || binary.LittleEndian.Uint16(b[14:]) == 3)
	case "FTEX":
		return prefix("FTEX")
	case "GRIB":
		return len(b) >= 8 && prefix("GRIB") && b[7] == 1
	case "HDF5":
		return prefix("\x89HDF\r\n\x1a\n")
	case "JPEG2000":
		return prefix("\xff\x4f\xff\x51") || prefix("\x00\x00\x00\x0cjP  \x0d\x0a\x87\x0a")
	case "ICNS":
		return prefix("icns")
	case "IM", "IMT":
		first := b[:min(100, len(b))]
		if !bytes.Contains(first, []byte{'\n'}) {
			return false
		}
		if name == "IM" {
			first = bytes.TrimLeft(first, "\r")
			if len(first) == 0 {
				return false
			}
			if !(first[0] >= 'A' && first[0] <= 'Z' || first[0] >= 'a' && first[0] <= 'z') {
				return false
			}
			end := bytes.IndexByte(first, '\n')
			if end >= 0 {
				first = first[:end]
			}
			return bytes.Contains(first, []byte{':'})
		}
		return len(first) > 0 && (first[0] == '*' || first[0] == ' ' || first[0] >= 'a' && first[0] <= 'z')
	case "IPTC":
		return len(b) >= 3 && b[0] == 28 && (b[1] >= 1 && b[1] <= 9 || b[1] == 240)
	case "MCIDAS":
		return prefix("\x00\x00\x00\x00\x00\x00\x00\x04")
	case "MPEG":
		return prefix("\x00\x00\x01\xb3")
	case "TIFF":
		return prefix("II*\x00") || prefix("MM\x00*") || prefix("II+\x00") || prefix("MM\x00+")
	case "PSD":
		return prefix("8BPS")
	case "SPIDER":
		if len(b) < 108 {
			return false
		}
		for _, order := range []binary.ByteOrder{binary.BigEndian, binary.LittleEndian} {
			var h [28]float64
			for i := 1; i <= 27; i++ {
				h[i] = float64(math.Float32frombits(order.Uint32(b[(i-1)*4:])))
			}
			valid := true
			for _, i := range []int{1, 2, 5, 12, 13, 22, 23} {
				if math.IsNaN(h[i]) || math.IsInf(h[i], 0) || math.Trunc(h[i]) != h[i] {
					valid = false
					break
				}
			}
			if valid && (h[5] == 1 || h[5] == 3 || h[5] == -11 || h[5] == -12 || h[5] == -21 || h[5] == -22) && h[22] != 0 && h[22] == h[13]*h[23] {
				return true
			}
		}
		return false
	case "WMF":
		return prefix("\xd7\xcd\xc6\x9a\x00\x00") || prefix("\x01\x00\x00\x00")
	case "XPM":
		return prefix("/* XPM */")
	case "XVTHUMB":
		return prefix("P7 332")
	}
	return false
}

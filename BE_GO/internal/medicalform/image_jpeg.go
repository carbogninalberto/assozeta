// JPEG opening as libjpeg's jpeg_read_header does it for fz_load_jpeg_info:
// the marker segments up to the start of scan are parsed and validated, and
// the scan data itself is never read here. MuPDF embeds the file as a
// DCTDecode stream, so the drawn samples are the file's own.
package medicalform

func decodeJPEG(file []byte) (imageAsset, bool) {
	if len(file) < 4 || file[0] != 0xff || file[1] != 0xd8 {
		return imageAsset{}, false
	}
	width, height, components, ok := jpegHeader(file)
	if !ok {
		return imageAsset{}, false
	}
	asset := imageAsset{width: width, height: height, jpeg: file}
	switch components {
	case 1:
		asset.gray = true
	case 3:
	case 4:
		asset.cmyk = true
	default:
		return imageAsset{}, false
	}
	return asset, true
}

// jpegHeader walks the marker segments to the first start-of-scan marker,
// validating the lengths, the frame and the Huffman tables the way libjpeg
// does while reading the header.
func jpegHeader(file []byte) (width, height, components int, ok bool) {
	p := 2
	haveFrame := false
	for {
		if p >= len(file) {
			return 0, 0, 0, false
		}
		if file[p] != 0xff {
			return 0, 0, 0, false
		}
		for p < len(file) && file[p] == 0xff {
			p++
		}
		if p >= len(file) {
			return 0, 0, 0, false
		}
		marker := file[p]
		p++
		switch {
		case marker == 0x01 || marker >= 0xd0 && marker <= 0xd7:
			continue
		case marker == 0xd8:
			continue
		case marker == 0xd9:
			// End of image before a scan: libjpeg reports a missing SOS.
			return 0, 0, 0, false
		case marker == 0x00:
			return 0, 0, 0, false
		}
		if p+2 > len(file) {
			return 0, 0, 0, false
		}
		length := int(file[p])<<8 | int(file[p+1])
		if length < 2 || p+length > len(file) {
			return 0, 0, 0, false
		}
		body := file[p+2 : p+length]
		switch marker {
		case 0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
			0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf: // SOFn
			if len(body) < 6 {
				return 0, 0, 0, false
			}
			height = int(body[1])<<8 | int(body[2])
			width = int(body[3])<<8 | int(body[4])
			components = int(body[5])
			if width <= 0 || height <= 0 || components <= 0 || len(body) < 6+components*3 {
				return 0, 0, 0, false
			}
			haveFrame = true
		case 0xc4: // DHT
			if !jpegHuffmanTablesOK(body) {
				return 0, 0, 0, false
			}
		case 0xda: // SOS
			if !haveFrame || len(body) < 1 {
				return 0, 0, 0, false
			}
			scans := int(body[0])
			if scans < 1 || scans > 4 || len(body) < 1+scans*2+3 {
				return 0, 0, 0, false
			}
			return width, height, components, true
		}
		p += length
	}
}

// jpegHuffmanTablesOK is get_dht's check: a table with more than 256 symbols
// is "Bogus Huffman table definition"; the table data must fit the segment.
func jpegHuffmanTablesOK(body []byte) bool {
	for len(body) > 0 {
		if len(body) < 17 {
			return false
		}
		index := body[0] & 0x0f
		count := 0
		for i := 1; i <= 16; i++ {
			count += int(body[i])
		}
		if count > 256 {
			return false
		}
		// The definitions are stored only for the four real table slots;
		// others are skipped after being sized.
		_ = index
		if len(body) < 17+count {
			return false
		}
		body = body[17+count:]
	}
	return true
}

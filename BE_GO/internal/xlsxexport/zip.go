package xlsxexport

import (
	"archive/zip"
	"bytes"
	"compress/flate"
	"hash/crc32"
	"time"
)

// zipEntry is one part of the package with Python zipfile's header values.
type zipEntry struct {
	name     string
	data     []byte
	external uint32 // external_attr
}

const (
	externalFile    = 0o600 << 16              // zipfile.writestr of a name: ZipInfo default
	externalRegular = (0o100000 | 0o600) << 16 // xlsxwriter, and openpyxl's worksheets
)

// writeZip stores the entries in order as Python's zipfile does with
// ZIP_DEFLATED: create_system 3, versions 20, flags 0 (sizes in the local
// header, no data descriptor), no extra fields, local time stamps.
func writeZip(entries []zipEntry, now time.Time) ([]byte, error) {
	return writeZipFlags(entries, now, 0)
}

// zipDataDescriptor is general purpose flag bit 3: what zipfile writes to a
// stream it cannot seek (wb.save(HttpResponse)): zero CRC and sizes in the
// local header, then a signed data descriptor after the data.
const zipDataDescriptor = 0x8

func writeZipFlags(entries []zipEntry, now time.Time, flags uint16) ([]byte, error) {
	var out bytes.Buffer
	writer := zip.NewWriter(&out)
	local := now.Local()
	date := uint16((local.Year()-1980)<<9 | int(local.Month())<<5 | local.Day())
	clock := uint16(local.Hour()<<11 | local.Minute()<<5 | local.Second()/2)
	for _, entry := range entries {
		var compressed bytes.Buffer
		deflate, err := flate.NewWriter(&compressed, 6)
		if err != nil {
			return nil, err
		}
		if _, err := deflate.Write(entry.data); err != nil {
			return nil, err
		}
		if err := deflate.Close(); err != nil {
			return nil, err
		}
		header := &zip.FileHeader{
			Name:               entry.name,
			Method:             zip.Deflate,
			Flags:              flags,
			CreatorVersion:     3<<8 | 20,
			ReaderVersion:      20,
			ExternalAttrs:      entry.external,
			CRC32:              crc32.ChecksumIEEE(entry.data),
			CompressedSize64:   uint64(compressed.Len()),
			UncompressedSize64: uint64(len(entry.data)),
			ModifiedDate:       date,
			ModifiedTime:       clock,
		}
		part, err := writer.CreateRaw(header)
		if err != nil {
			return nil, err
		}
		if _, err := part.Write(compressed.Bytes()); err != nil {
			return nil, err
		}
	}
	if err := writer.Close(); err != nil {
		return nil, err
	}
	return out.Bytes(), nil
}

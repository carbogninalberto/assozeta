package medicalform

// The incremental update that adds PyMuPDF's images to the filled form: the
// page tree is read from FillBiella's own output, the image XObjects and their
// content stream are appended as new objects, the first page object is
// rewritten with them and a cross-reference section chained to the previous
// one closes the file.

import (
	"bytes"
	"compress/zlib"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"
)

// ─── incremental update ─────────────────────────────────────────────────────

const pdfDelimiters = "()<>[]{}/%"

type pdfNode struct {
	kind  byte // 'd' dictionary, 'a' array, 'r' reference, 'k' keyword or scalar
	start int
	end   int
	num   int
	keys  []string
	items []pdfNode
}

type pdfDocument struct {
	data     []byte
	offsets  map[int]int
	xrefAt   int
	size     int
	root     int
	info     int
	hasInfo  bool
	next     int
	newXrefs []pdfXref
}

type pdfXref struct {
	num    int
	offset int
}

var errPDF = errors.New("medicalform: unreadable form structure")

func parsePDF(data []byte) (*pdfDocument, error) {
	doc := &pdfDocument{data: data, offsets: map[int]int{}}
	// startxref and the table it points at.
	start := bytes.LastIndex(data, []byte("startxref"))
	if start < 0 {
		return nil, errPDF
	}
	cursor := skipPDFSpace(data, start+len("startxref"))
	offset, cursor, err := parsePDFInt(data, cursor)
	if err != nil || offset < 0 || offset >= len(data) {
		return nil, errPDF
	}
	doc.xrefAt = offset
	position := skipPDFSpace(data, offset)
	if !bytes.HasPrefix(data[position:], []byte("xref")) {
		return nil, errPDF // cross-reference streams are not this form's output
	}
	position = skipPDFSpace(data, position+len("xref"))
	for {
		if bytes.HasPrefix(data[position:], []byte("trailer")) {
			break
		}
		first, next, err := parsePDFInt(data, position)
		if err != nil {
			return nil, errPDF
		}
		count, next, err := parsePDFInt(data, next)
		if err != nil {
			return nil, errPDF
		}
		next = skipPDFSpace(data, next)
		for i := 0; i < count; i++ {
			if next+20 > len(data) {
				return nil, errPDF
			}
			entryOffset, err := strconv.Atoi(strings.TrimSpace(string(data[next : next+10])))
			if err != nil {
				return nil, errPDF
			}
			// The entry is 20 bytes: offset, generation, kind.
			if data[next+17] == 'n' {
				doc.offsets[first+i] = entryOffset
			}
			next += 20
		}
		position = skipPDFSpace(data, next)
	}
	position = skipPDFSpace(data, position+len("trailer"))
	trailer, _, err := parsePDFValue(data, position)
	if err != nil || trailer.kind != 'd' {
		return nil, errPDF
	}
	rootNode, ok := trailer.lookup("/Root")
	if !ok || rootNode.kind != 'r' {
		return nil, errPDF
	}
	doc.root = rootNode.num
	if infoNode, ok := trailer.lookup("/Info"); ok && infoNode.kind == 'r' {
		doc.info, doc.hasInfo = infoNode.num, true
	}
	sizeNode, ok := trailer.lookup("/Size")
	if !ok {
		return nil, errPDF
	}
	doc.size, err = strconv.Atoi(string(data[sizeNode.start:sizeNode.end]))
	if err != nil || doc.size <= 0 {
		return nil, errPDF
	}
	doc.next = doc.size
	return doc, nil
}

// object parses the object at a cross-reference entry.
func (d *pdfDocument) object(num int) (pdfNode, error) {
	offset, known := d.offsets[num]
	if !known || offset < 0 || offset >= len(d.data) {
		return pdfNode{}, errPDF
	}
	cursor := skipPDFSpace(d.data, offset)
	header, cursor, err := parsePDFInt(d.data, cursor)
	if err != nil || header != num {
		return pdfNode{}, errPDF
	}
	if _, cursor, err = parsePDFInt(d.data, cursor); err != nil { // generation
		return pdfNode{}, errPDF
	}
	cursor = skipPDFSpace(d.data, cursor)
	if !bytes.HasPrefix(d.data[cursor:], []byte("obj")) {
		return pdfNode{}, errPDF
	}
	cursor = skipPDFSpace(d.data, cursor+len("obj"))
	value, _, err := parsePDFValue(d.data, cursor)
	if err != nil {
		return pdfNode{}, errPDF
	}
	return value, nil
}

func (d *pdfDocument) resolve(node pdfNode) (pdfNode, error) {
	if node.kind != 'r' {
		return node, nil
	}
	return d.object(node.num)
}

// stream is the data of a stream object.
func (d *pdfDocument) stream(node pdfNode) ([]byte, error) {
	object, err := d.object(node.num)
	if err != nil || object.kind != 'd' {
		return nil, errPDF
	}
	lengthNode, ok := object.lookup("/Length")
	if !ok {
		return nil, errPDF
	}
	if lengthNode.kind == 'r' {
		if lengthNode, err = d.resolve(lengthNode); err != nil {
			return nil, err
		}
	}
	length, err := strconv.Atoi(string(d.data[lengthNode.start:lengthNode.end]))
	if err != nil || length < 0 || object.end+1 > len(d.data) {
		return nil, errPDF
	}
	cursor := skipPDFSpace(d.data, object.end)
	if !bytes.HasPrefix(d.data[cursor:], []byte("stream")) {
		return nil, errPDF
	}
	cursor += len("stream")
	if cursor < len(d.data) && d.data[cursor] == '\r' {
		cursor++
	}
	if cursor < len(d.data) && d.data[cursor] == '\n' {
		cursor++
	}
	if cursor+length > len(d.data) {
		return nil, errPDF
	}
	return d.data[cursor : cursor+length], nil
}

// lookup finds a key of a dictionary node.
func (n pdfNode) lookup(key string) (pdfNode, bool) {
	if n.kind != 'd' {
		return pdfNode{}, false
	}
	for i, name := range n.keys {
		if name == key {
			return n.items[i], true
		}
	}
	return pdfNode{}, false
}

// overlayImages adds the images after the first page's content, as
// Page.insert_image does: wrap_contents balances the graphics state, the image
// XObjects join the page's /XObject resources and a new content stream draws
// each of them with its matrix.
func overlayImages(filled []byte, images []Image) ([]byte, error) {
	doc, err := parsePDF(filled)
	if err != nil {
		return nil, err
	}
	page, pageNum, err := doc.firstPage()
	if err != nil {
		return nil, err
	}
	box, err := doc.pageBox(page, "/MediaBox")
	if err != nil {
		return nil, err
	}
	rotation, err := doc.pageInt(page, "/Rotate")
	if err != nil {
		return nil, err
	}
	crop, err := doc.pageBox(page, "/CropBox")
	if err != nil {
		return nil, err
	}
	contents, err := doc.pageContents(page)
	if err != nil {
		return nil, err
	}
	used, err := doc.pageNames(page)
	if err != nil {
		return nil, err
	}

	out := &bytes.Buffer{}
	out.Write(filled)
	if last := filled[len(filled)-1]; last != '\n' {
		out.WriteByte('\n')
	}
	write := func(num int, body []byte) int {
		offset := out.Len()
		fmt.Fprintf(out, "%d 0 obj\n", num)
		out.Write(body)
		out.WriteString("\nendobj\n")
		doc.newXrefs = append(doc.newXrefs, pdfXref{num: num, offset: offset})
		return num
	}

	// wrap_contents: an unbalanced content state is closed around the page's
	// own content, so that the inserted images start from a balanced one.
	push, pop := qBalance(doc, contents)
	if push > 0 {
		contents = append([]int{write(doc.alloc(), contentBody([]byte(strings.Repeat("q\n", push))))}, contents...)
	}

	var operations bytes.Buffer
	if pop > 0 {
		operations.WriteString(strings.Repeat("\nQ", pop) + "\n")
	}
	xobjects := make([]string, 0, len(images))
	for index, image := range images {
		asset, ok := decodeImage(image.file)
		if !ok {
			return nil, errImage
		}
		name := "fzImg" + strconv.Itoa(index)
		if used[name] {
			return nil, errPDF
		}
		used[name] = true
		number := write(doc.alloc(), doc.imageBody(asset, write))
		xobjects = append(xobjects, "/"+name+" "+strconv.Itoa(number)+" 0 R")
		matrix := imageMatrix(asset.width, asset.height, image.rect, box, crop, rotation)
		fmt.Fprintf(&operations, "\nq\n%s cm\n/%s Do\nQ\n", formatMatrix(matrix), name)
	}
	contents = append(contents, write(doc.alloc(), contentBody(operations.Bytes())))

	// The page object: /Contents with the added streams and /Resources with
	// the image XObjects.
	resources, resourceNum, err := doc.pageResources(page)
	if err != nil {
		return nil, err
	}
	pageEdits := []pdfEdit{
		{start: contentsNode(page).start, end: contentsNode(page).end, text: formatRefs(contents)},
	}
	add := func(body []byte) int { return write(doc.alloc(), body) }
	if resourceNum > 0 {
		// The page's /Resources is its own object: it is rewritten in place.
		write(resourceNum, doc.resourcesBody(resources, xobjects, add))
	} else {
		body, err := doc.appendXObjects(resources, xobjects, add)
		if err != nil {
			return nil, err
		}
		pageEdits = append(pageEdits, pdfEdit{start: resources.start, end: resources.end, text: body})
	}
	pageValue, err := replaceEdits(doc.data[page.start:page.end], page.start, pageEdits)
	if err != nil {
		return nil, err
	}
	write(pageNum, pageValue)

	// Cross-reference section and trailer, chaining to the previous one.
	xrefAt := out.Len()
	out.WriteString("xref\n")
	sort.Slice(doc.newXrefs, func(i, j int) bool { return doc.newXrefs[i].num < doc.newXrefs[j].num })
	for start := 0; start < len(doc.newXrefs); {
		end := start + 1
		for end < len(doc.newXrefs) && doc.newXrefs[end].num == doc.newXrefs[end-1].num+1 {
			end++
		}
		fmt.Fprintf(out, "%d %d\n", doc.newXrefs[start].num, end-start)
		for _, entry := range doc.newXrefs[start:end] {
			fmt.Fprintf(out, "%010d 00000 n \n", entry.offset)
		}
		start = end
	}
	fmt.Fprintf(out, "trailer\n<< /Size %d /Root %d 0 R", doc.next, doc.root)
	if doc.hasInfo {
		fmt.Fprintf(out, " /Info %d 0 R", doc.info)
	}
	fmt.Fprintf(out, " /Prev %d >>\nstartxref\n%d\n%%%%EOF\n", doc.xrefAt, xrefAt)
	return out.Bytes(), nil
}

func (d *pdfDocument) alloc() int {
	number := d.next
	d.next++
	return number
}

// firstPage is the first page object of the page tree.
func (d *pdfDocument) firstPage() (pdfNode, int, error) {
	catalog, err := d.object(d.root)
	if err != nil || catalog.kind != 'd' {
		return pdfNode{}, 0, errPDF
	}
	pages, ok := catalog.lookup("/Pages")
	if !ok || pages.kind != 'r' {
		return pdfNode{}, 0, errPDF
	}
	node := pages
	for depth := 0; depth < 64; depth++ {
		object, err := d.object(node.num)
		if err != nil || object.kind != 'd' {
			return pdfNode{}, 0, errPDF
		}
		kids, ok := object.lookup("/Kids")
		if !ok {
			return object, node.num, nil
		}
		kids, err = d.resolve(kids)
		if err != nil || kids.kind != 'a' || len(kids.items) == 0 {
			return pdfNode{}, 0, errPDF
		}
		node = kids.items[0]
		if node.kind != 'r' {
			return pdfNode{}, 0, errPDF
		}
	}
	return pdfNode{}, 0, errPDF
}

// pageKey resolves a page attribute, inherited from the parent page tree
// nodes when the page does not carry it.
func (d *pdfDocument) pageKey(page pdfNode, key string) (pdfNode, bool, error) {
	for depth := 0; depth < 64; depth++ {
		if value, ok := page.lookup(key); ok {
			resolved, err := d.resolve(value)
			return resolved, true, err
		}
		parent, ok := page.lookup("/Parent")
		if !ok || parent.kind != 'r' {
			break
		}
		object, err := d.object(parent.num)
		if err != nil || object.kind != 'd' {
			return pdfNode{}, false, errPDF
		}
		page = object
	}
	return pdfNode{}, false, nil
}

func (d *pdfDocument) pageBox(page pdfNode, key string) ([4]float64, error) {
	value, found, err := d.pageKey(page, key)
	if err != nil || !found || value.kind != 'a' || len(value.items) < 4 {
		return [4]float64{}, errPDF
	}
	var box [4]float64
	for i := 0; i < 4; i++ {
		number, err := strconv.ParseFloat(string(d.data[value.items[i].start:value.items[i].end]), 64)
		if err != nil {
			return [4]float64{}, errPDF
		}
		box[i] = number
	}
	return box, nil
}

// pageInt reads an optional integer page attribute (/Rotate), inherited from
// the parent page tree nodes; an absent one is 0.
func (d *pdfDocument) pageInt(page pdfNode, key string) (int, error) {
	value, found, err := d.pageKey(page, key)
	if err != nil || !found {
		return 0, err
	}
	number, err := strconv.Atoi(string(d.data[value.start:value.end]))
	if err != nil {
		return 0, errPDF
	}
	return number, nil
}

func (d *pdfDocument) pageContents(page pdfNode) ([]int, error) {
	value, found, err := d.pageKey(page, "/Contents")
	if err != nil || !found {
		return nil, errPDF
	}
	if value.kind == 'r' {
		if object, err := d.object(value.num); err == nil && object.kind == 'a' {
			value = object
		} else {
			return []int{value.num}, nil
		}
	}
	if value.kind != 'a' {
		return nil, errPDF
	}
	refs := make([]int, 0, len(value.items))
	for _, item := range value.items {
		if item.kind != 'r' {
			return nil, errPDF
		}
		refs = append(refs, item.num)
	}
	return refs, nil
}

// pageNames are the resource names Page.insert_image avoids: the page's
// images, XObjects and fonts.
func (d *pdfDocument) pageNames(page pdfNode) (map[string]bool, error) {
	names := map[string]bool{}
	resources, _, err := d.pageResources(page)
	if err != nil {
		return nil, err
	}
	for _, key := range []string{"/XObject", "/Font"} {
		group, ok := resources.lookup(key)
		if !ok {
			continue
		}
		if group.kind == 'r' {
			if group, err = d.resolve(group); err != nil {
				return nil, err
			}
		}
		if group.kind != 'd' {
			return nil, errPDF
		}
		for _, name := range group.keys {
			names[name] = true
		}
	}
	return names, nil
}

// pageResources is the page's /Resources dictionary, and the object number
// when it is an indirect object (shared with the other pages).
func (d *pdfDocument) pageResources(page pdfNode) (pdfNode, int, error) {
	value, ok := page.lookup("/Resources")
	var number int
	if ok && value.kind == 'r' {
		number = value.num
	}
	resolved, found, err := d.pageKey(page, "/Resources")
	if err != nil || !found || resolved.kind != 'd' {
		return pdfNode{}, 0, errPDF
	}
	return resolved, number, nil
}

// qBalance is the missing q and Q commands of a page's content: the deepest
// deficit of pops and the pushes the content leaves open. Page.insert_image's
// wrap_contents closes them around the page's own content so that the
// inserted image starts from a balanced graphics state.
func qBalance(doc *pdfDocument, contents []int) (int, int) {
	var state bytes.Buffer
	for _, number := range contents {
		data, err := doc.stream(pdfNode{kind: 'r', num: number})
		if err != nil {
			return 0, 0
		}
		state.Write(data)
		state.WriteByte('\n')
	}
	depth, low := 0, 0
	forEachOperator(state.Bytes(), func(operator string) {
		switch operator {
		case "q":
			depth++
		case "Q":
			depth--
			if depth < low {
				low = depth
			}
		}
	})
	push := -low
	return push, push + depth
}

// forEachOperator calls visit with each content-stream operator: a run of
// regular characters outside strings, hex strings, inline images and comments.
// forEachOperator calls visit with each content-stream operator: a run of
// regular characters outside strings, hex strings, names and comments. The
// form's content draws no inline image (BI ... ID ... EI), whose binary data
// would need its own scan.
func forEachOperator(data []byte, visit func(string)) {
	for i := 0; i < len(data); {
		switch c := data[i]; {
		case c == '%':
			for i < len(data) && data[i] != '\n' && data[i] != '\r' {
				i++
			}
		case c == '(':
			i = skipPDFString(data, i)
		case c == '<':
			if i+1 < len(data) && data[i+1] == '<' {
				i += 2
				continue
			}
			i++
			for i < len(data) && data[i] != '>' {
				i++
			}
			i++
		case c == '/':
			i++
			for i < len(data) && !isPDFDelimiter(data[i]) && !isPDFSpaceByte(data[i]) {
				i++
			}
		case isPDFDelimiter(c) || isPDFSpaceByte(c):
			i++
		default:
			start := i
			for i < len(data) && !isPDFDelimiter(data[i]) && !isPDFSpaceByte(data[i]) {
				i++
			}
			visit(string(data[start:i]))
		}
	}
}

func skipPDFString(data []byte, i int) int {
	depth := 0
	for i < len(data) {
		switch data[i] {
		case '\\':
			i += 2
			continue
		case '(':
			depth++
		case ')':
			depth--
			if depth == 0 {
				return i + 1
			}
		}
		i++
	}
	return i
}

// imageMatrix is calc_image_matrix(w, h, clip, 0, True) of PyMuPDF 1.25.5
// with clip = rect * ~page.transformation_matrix: the keep_proportion scale of
// the image in the box, centred, with the image's own aspect ratio.
func imageMatrix(width, height int, rect Rect, box, crop [4]float64, rotation int) [6]float64 {
	// Page.transformation_matrix for a page PyMuPDF reads as unrotated flips
	// y at the mediabox; a rotated page flips it at the cropbox height.
	var x0, y0, x1, y1 float64
	if rotation%360 == 0 {
		x0 = rect.X0 + box[0]
		x1 = rect.X1 + box[0]
		y0 = box[3] - rect.Y1
		y1 = box[3] - rect.Y0
	} else {
		x0 = rect.X0
		x1 = rect.X1
		y0 = crop[3] - rect.Y1
		y1 = crop[3] - rect.Y0
	}
	tw, th := x1-x0, y1-y0
	fw, fh := 1.0, 1.0
	if large := float64(max(width, height)); large > 0 {
		fw, fh = float64(width)/large, float64(height)/large
	}
	small := min(fw, fh)
	w, h := tw, th
	switch {
	case fw < 1:
		if tw/fw > th/fh {
			w, h = th*small, th
		} else {
			w, h = tw, tw/small
		}
	case fw != fh:
		if tw/fw > th/fh {
			w, h = th/small, th
		} else {
			w, h = tw, tw*small
		}
	}
	cx, cy := (x0+x1)/2, (y0+y1)/2
	// calc_image_matrix concatenates a (-0.5, -0.5) shift, the scale and the
	// translation: the image is centred in the clip rectangle.
	return [6]float64{w, 0, 0, h, cx - 0.5*w, cy - 0.5*h}
}

func formatMatrix(matrix [6]float64) string {
	parts := make([]string, len(matrix))
	for i, value := range matrix {
		parts[i] = strconv.FormatFloat(value, 'f', -1, 64)
	}
	return strings.Join(parts, " ")
}

func formatRefs(numbers []int) []byte {
	var out strings.Builder
	out.WriteByte('[')
	for _, number := range numbers {
		out.WriteString(" " + strconv.Itoa(number) + " 0 R")
	}
	out.WriteString(" ]")
	return []byte(out.String())
}

// ─── PDF value scanning ─────────────────────────────────────────────────────

func isPDFSpaceByte(c byte) bool {
	return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\f' || c == 0
}

func isPDFDelimiter(c byte) bool {
	return strings.IndexByte(pdfDelimiters, c) >= 0
}

func skipPDFSpace(data []byte, i int) int {
	for i < len(data) {
		if data[i] == '%' {
			for i < len(data) && data[i] != '\n' && data[i] != '\r' {
				i++
			}
			continue
		}
		if !isPDFSpaceByte(data[i]) {
			break
		}
		i++
	}
	return i
}

func parsePDFInt(data []byte, i int) (int, int, error) {
	i = skipPDFSpace(data, i)
	start := i
	if i < len(data) && (data[i] == '+' || data[i] == '-') {
		i++
	}
	for i < len(data) && data[i] >= '0' && data[i] <= '9' {
		i++
	}
	if i == start {
		return 0, i, errPDF
	}
	value, err := strconv.Atoi(string(data[start:i]))
	return value, i, err
}

func parsePDFValue(data []byte, i int) (pdfNode, int, error) {
	i = skipPDFSpace(data, i)
	if i >= len(data) {
		return pdfNode{}, i, errPDF
	}
	switch c := data[i]; c {
	case '<':
		if i+1 < len(data) && data[i+1] == '<' {
			node := pdfNode{kind: 'd', start: i}
			i = skipPDFSpace(data, i+2)
			for i+1 < len(data) && !(data[i] == '>' && data[i+1] == '>') {
				if data[i] != '/' {
					return pdfNode{}, i, errPDF
				}
				key, next, err := parsePDFName(data, i)
				if err != nil {
					return pdfNode{}, i, err
				}
				value, next, err := parsePDFValue(data, next)
				if err != nil {
					return pdfNode{}, i, err
				}
				node.keys = append(node.keys, key)
				node.items = append(node.items, value)
				i = skipPDFSpace(data, next)
			}
			if i+1 >= len(data) {
				return pdfNode{}, i, errPDF
			}
			node.end = i + 2
			return node, node.end, nil
		}
		start := i
		for i < len(data) && data[i] != '>' {
			i++
		}
		if i >= len(data) {
			return pdfNode{}, i, errPDF
		}
		return pdfNode{kind: 'k', start: start, end: i + 1}, i + 1, nil
	case '[':
		node := pdfNode{kind: 'a', start: i}
		i = skipPDFSpace(data, i+1)
		for i < len(data) && data[i] != ']' {
			value, next, err := parsePDFValue(data, i)
			if err != nil {
				return pdfNode{}, i, err
			}
			node.items = append(node.items, value)
			i = skipPDFSpace(data, next)
		}
		if i >= len(data) {
			return pdfNode{}, i, errPDF
		}
		node.end = i + 1
		return node, node.end, nil
	case '/':
		start := i
		_, end, err := parsePDFName(data, i)
		if err != nil {
			return pdfNode{}, i, err
		}
		return pdfNode{kind: 'k', start: start, end: end}, end, nil
	case '(':
		end := skipPDFString(data, i)
		if end == i {
			return pdfNode{}, i, errPDF
		}
		return pdfNode{kind: 'k', start: i, end: end}, end, nil
	}
	// A number, possibly the first part of a reference.
	start := i
	if data[i] == '+' || data[i] == '-' {
		i++
	}
	digits := 0
	for i < len(data) && (data[i] >= '0' && data[i] <= '9' || data[i] == '.') {
		if data[i] != '.' {
			digits++
		}
		i++
	}
	if digits == 0 || i < len(data) && !isPDFSpaceByte(data[i]) && !isPDFDelimiter(data[i]) {
		// true, false, null, an operator or something this form does not use.
		for i < len(data) && !isPDFSpaceByte(data[i]) && !isPDFDelimiter(data[i]) {
			i++
		}
		return pdfNode{kind: 'k', start: start, end: i}, i, nil
	}
	number := strings.TrimSpace(string(data[start:i]))
	if first, err := strconv.Atoi(number); err == nil {
		// "number generation R": a reference.
		cursor := skipPDFSpace(data, i)
		second, next, err := parsePDFInt(data, cursor)
		if err == nil {
			next = skipPDFSpace(data, next)
			if next < len(data) && data[next] == 'R' && (next+1 == len(data) || isPDFSpaceByte(data[next+1]) || isPDFDelimiter(data[next+1])) {
				if second >= 0 {
					return pdfNode{kind: 'r', start: start, end: next + 1, num: first}, next + 1, nil
				}
			}
		}
	}
	return pdfNode{kind: 'k', start: start, end: i}, i, nil
}

func parsePDFName(data []byte, i int) (string, int, error) {
	if i >= len(data) || data[i] != '/' {
		return "", i, errPDF
	}
	start := i + 1
	i = start
	for i < len(data) && !isPDFDelimiter(data[i]) && !isPDFSpaceByte(data[i]) {
		i++
	}
	raw := string(data[start:i])
	if strings.Contains(raw, "#") {
		var decoded strings.Builder
		for j := 0; j < len(raw); j++ {
			if raw[j] == '#' && j+2 < len(raw) {
				if value, err := strconv.ParseUint(raw[j+1:j+3], 16, 8); err == nil {
					decoded.WriteByte(byte(value))
					j += 2
					continue
				}
			}
			decoded.WriteByte(raw[j])
		}
		raw = "/" + decoded.String()
	} else {
		raw = "/" + raw
	}
	return raw, i, nil
}

// ─── object bodies ──────────────────────────────────────────────────────────

func (d *pdfDocument) imageBody(asset imageAsset, write func(int, []byte) int) []byte {
	var body bytes.Buffer
	if asset.jpeg != nil {
		colour := "/DeviceRGB"
		switch {
		case asset.gray:
			colour = "/DeviceGray"
		case asset.cmyk:
			colour = "/DeviceCMYK"
		}
		fmt.Fprintf(&body, "<< /Type /XObject /Subtype /Image /Width %d /Height %d /BitsPerComponent 8",
			asset.width, asset.height)
		body.WriteString(" /ColorSpace " + colour)
		if asset.cmyk {
			// MuPDF writes CMYK JPEGs inverted, as Adobe's encoder does.
			body.WriteString(" /Decode [1 0 1 0 1 0 1 0]")
		}
		fmt.Fprintf(&body, " /Filter /DCTDecode /Length %d >>\nstream\n", len(asset.jpeg))
		body.Write(asset.jpeg)
		body.WriteString("\nendstream")
		return body.Bytes()
	}
	colour := "/DeviceRGB"
	switch {
	case asset.gray:
		colour = "/DeviceGray"
	case asset.cmyk:
		colour = "/DeviceCMYK"
	}
	fmt.Fprintf(&body, "<< /Type /XObject /Subtype /Image /Width %d /Height %d /BitsPerComponent 8 /ColorSpace %s /Filter /FlateDecode /Length %d",
		asset.width, asset.height, colour, len(flate(asset.color)))
	if asset.alpha {
		mask := write(d.alloc(), imageMaskBody(asset))
		fmt.Fprintf(&body, " /SMask %d 0 R", mask)
	}
	body.WriteString(" >>\nstream\n")
	body.Write(flate(asset.color))
	body.WriteString("\nendstream")
	return body.Bytes()
}

func imageMaskBody(asset imageAsset) []byte {
	var body bytes.Buffer
	data := flate(asset.mask)
	fmt.Fprintf(&body, "<< /Type /XObject /Subtype /Image /Width %d /Height %d /BitsPerComponent %d /ColorSpace /DeviceGray /Filter /FlateDecode /Length %d >>\nstream\n",
		asset.width, asset.height, asset.maskBits, len(data))
	body.Write(data)
	body.WriteString("\nendstream")
	return body.Bytes()
}

func flate(data []byte) []byte {
	var out bytes.Buffer
	writer := zlib.NewWriter(&out)
	_, _ = writer.Write(data)
	_ = writer.Close()
	return out.Bytes()
}

// contentBody is a content stream object.
func contentBody(data []byte) []byte {
	var body bytes.Buffer
	fmt.Fprintf(&body, "<< /Length %d >>\nstream\n", len(data))
	body.Write(data)
	body.WriteString("\nendstream")
	return body.Bytes()
}

func (d *pdfDocument) resourcesBody(resources pdfNode, xobjects []string, add func([]byte) int) []byte {
	body, err := d.appendXObjects(resources, xobjects, add)
	if err != nil {
		return d.data[resources.start:resources.end]
	}
	return body
}

// appendXObjects is the /Resources dictionary with the image XObjects added to
// its /XObject dictionary (a copy of that object when it is indirect).
func (d *pdfDocument) appendXObjects(resources pdfNode, xobjects []string, add func([]byte) int) ([]byte, error) {
	if resources.kind != 'd' {
		return nil, errPDF
	}
	entries := " " + strings.Join(xobjects, " ") + " "
	value, ok := resources.lookup("/XObject")
	if !ok {
		return replaceEdits(d.data[resources.start:resources.end], resources.start,
			[]pdfEdit{{start: resources.end - 2, end: resources.end - 2, text: []byte("/XObject <<" + entries + ">> ")}})
	}
	group, err := d.resolve(value)
	if err != nil || group.kind != 'd' {
		return nil, errPDF
	}
	if value.kind == 'r' {
		// The XObject dictionary is its own object: write a copy that the page's
		// /Resources points at.
		body, err := replaceEdits(d.data[group.start:group.end], group.start,
			[]pdfEdit{{start: group.end - 2, end: group.end - 2, text: []byte(entries)}})
		if err != nil {
			return nil, err
		}
		number := add(body)
		return replaceEdits(d.data[resources.start:resources.end], resources.start,
			[]pdfEdit{{start: value.start, end: value.end, text: []byte(strconv.Itoa(number) + " 0 R")}})
	}
	return replaceEdits(d.data[resources.start:resources.end], resources.start,
		[]pdfEdit{{start: group.end - 2, end: group.end - 2, text: []byte(entries)}})
}

type pdfEdit struct {
	start, end int
	text       []byte
}

// replaceEdits writes text over byte spans of base[start:end] (given in the
// document's own offsets).
func replaceEdits(base []byte, offset int, edits []pdfEdit) ([]byte, error) {
	sort.Slice(edits, func(i, j int) bool { return edits[i].start > edits[j].start })
	out := append([]byte(nil), base...)
	last := len(out) + offset
	for _, edit := range edits {
		start, end := edit.start-offset, edit.end-offset
		if start < 0 || end < start || end > last || end > len(out) {
			return nil, errPDF
		}
		out = append(out[:start], append(append([]byte(nil), edit.text...), out[end:]...)...)
		last = start
	}
	return out, nil
}

// contentsNode is the page's own /Contents value.
func contentsNode(page pdfNode) pdfNode {
	value, _ := page.lookup("/Contents")
	return value
}

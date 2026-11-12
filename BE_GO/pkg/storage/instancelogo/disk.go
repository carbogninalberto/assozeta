package instancelogo

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var ErrStorage = errors.New("instance logo storage unavailable")

// Disk is an explicitly injected FileSystemStorage adapter. The existing
// production configuration selects S3 even when STORE_ON_DISK is true.
type Disk struct {
	root      string
	admission admission
}

func NewDisk(root string) (*Disk, error) {
	absolute, err := filepath.Abs(root)
	if err != nil {
		return nil, err
	}
	return &Disk{root: absolute, admission: newAdmission(4)}, nil
}
func (d *Disk) name(key string) (string, error) {
	name := filepath.Join(d.root, filepath.FromSlash(key))
	relative, err := filepath.Rel(d.root, name)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) || strings.ContainsRune(key, 0) || filepath.IsAbs(key) {
		return "", ErrStorage
	}
	return name, nil
}
func (d *Disk) Exists(ctx context.Context, key string) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	name, err := d.name(key)
	if err != nil {
		return false, err
	}
	// FileSystemStorage uses os.path.lexists: broken links exist; filesystem
	// lookup errors themselves become false, while unsafe path construction fails.
	_, err = os.Lstat(name)
	return err == nil, nil
}
func (d *Disk) Open(ctx context.Context, key string) (domain.InstanceLogoFile, error) {
	release, err := d.admission.acquire(ctx)
	if err != nil {
		return domain.InstanceLogoFile{}, err
	}
	transferred := false
	defer func() {
		if !transferred {
			release()
		}
	}()
	name, err := d.name(key)
	if err != nil {
		return domain.InstanceLogoFile{}, err
	}
	file, err := os.Open(name)
	if err != nil {
		return domain.InstanceLogoFile{}, err
	}
	stat, err := file.Stat()
	if err != nil || stat.IsDir() {
		file.Close()
		return domain.InstanceLogoFile{}, ErrStorage
	}
	if err := ctx.Err(); err != nil {
		file.Close()
		return domain.InstanceLogoFile{}, err
	}
	transferred = true
	return domain.InstanceLogoFile{Body: ownFile(ctx, file, "", release), Size: stat.Size()}, nil
}

var _ domain.InstanceLogoStorage = (*Disk)(nil)

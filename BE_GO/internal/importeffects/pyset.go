package importeffects

import (
	"math/big"
	"strings"
)

// pySetOrder returns keys (canonical UUID texts, in insertion order, possibly
// repeated) in the iteration order of a CPython 3.13 set of uuid.UUID objects
// built by adding them one by one: Django's ManyRelatedManager._add_items
// collects the target ids in such a set and bulk-creates the through rows in
// its order, which fixes their serial ids. hash(UUID) is hash(int), the int
// modulo 2**61 - 1 on 64-bit builds (Objects/setobject.c, set_add_entry,
// set_table_resize and set_insert_clean).
func pySetOrder(keys []string) []string {
	const linearProbes, perturbShift = 9, 5
	type entry struct {
		key  string
		hash uint64
		used bool
	}
	table := make([]entry, 8)
	fill := 0
	insertClean := func(table []entry, key string, hash uint64) {
		mask := uint64(len(table) - 1)
		perturb := hash
		i := hash & mask
		for {
			if !table[i].used {
				table[i] = entry{key: key, hash: hash, used: true}
				return
			}
			if i+linearProbes <= mask {
				for j := uint64(1); j <= linearProbes; j++ {
					if !table[i+j].used {
						table[i+j] = entry{key: key, hash: hash, used: true}
						return
					}
				}
			}
			perturb >>= perturbShift
			i = (i*5 + 1 + perturb) & mask
		}
	}
	for _, key := range keys {
		hash := pyUUIDHash(key)
		mask := uint64(len(table) - 1)
		perturb := hash
		i := hash & mask
		found := false
	probe:
		for {
			probes := uint64(0)
			if i+linearProbes <= mask {
				probes = linearProbes
			}
			for j := uint64(0); j <= probes; j++ {
				slot := &table[i+j]
				if !slot.used {
					*slot = entry{key: key, hash: hash, used: true}
					break probe
				}
				if slot.hash == hash && slot.key == key {
					found = true
					break probe
				}
			}
			perturb >>= perturbShift
			i = (i*5 + 1 + perturb) & mask
		}
		if found {
			continue
		}
		fill++
		if uint64(fill)*5 < mask*3 {
			continue
		}
		minused := fill * 4
		if fill > 50000 {
			minused = fill * 2
		}
		size := 8
		for size <= minused {
			size <<= 1
		}
		resized := make([]entry, size)
		for _, old := range table {
			if old.used {
				insertClean(resized, old.key, old.hash)
			}
		}
		table = resized
	}
	out := make([]string, 0, fill)
	for _, slot := range table {
		if slot.used {
			out = append(out, slot.key)
		}
	}
	return out
}

var pyHashModulus = new(big.Int).Sub(new(big.Int).Lsh(big.NewInt(1), 61), big.NewInt(1))

func pyUUIDHash(key string) uint64 {
	n, _ := new(big.Int).SetString(strings.ReplaceAll(key, "-", ""), 16)
	return new(big.Int).Mod(n, pyHashModulus).Uint64()
}

// PySetOrder is pySetOrder for other packages: the iteration order of the
// set Django's many-to-many add() builds from the new target ids.
func PySetOrder(keys []string) []string { return pySetOrder(keys) }

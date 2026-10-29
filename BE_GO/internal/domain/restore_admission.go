package domain

import "errors"

var (
	ErrRestoreBusy              = errors.New("Un’altra operazione sui dati è in corso. Riprova tra poco.")
	ErrRestoreVolumeUnavailable = errors.New("Il volume di coordinamento non è disponibile.")
	ErrRestorePending           = errors.New("Ripristino dei dati in corso. Le modifiche sono temporaneamente sospese.")
	ErrRestoreLockRequired      = errors.New("Il volume di coordinamento del ripristino non è configurato. Aggiorna la distribuzione self-hosted.")
)

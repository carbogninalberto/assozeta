//go:build darwin || freebsd || netbsd || openbsd

package main

import "golang.org/x/sys/unix"

const (
	getTermios = unix.TIOCGETA
	setTermios = unix.TIOCSETAF // tcsetattr(TCSAFLUSH)
)

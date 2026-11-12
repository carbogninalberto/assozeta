// This test-only helper supplies Linux-kernel lock and transaction barriers.
// It is mounted read-only; the worker and migration executables come from image.
package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"net/url"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
)

func main() {
	if len(os.Args) != 2 {
		os.Exit(2)
	}
	if err := run(os.Args[1]); err != nil {
		fmt.Fprintln(os.Stderr, "image fixture barrier failed")
		os.Exit(1)
	}
}
func run(mode string) error {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	switch mode {
	case "keys":
		public, private, err := ed25519.GenerateKey(rand.Reader)
		if err != nil {
			return err
		}
		secret, err := x509.MarshalPKCS8PrivateKey(private)
		if err != nil {
			return err
		}
		visible, err := x509.MarshalPKIXPublicKey(public)
		if err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]string{"JWT_SECRET_KEY_B64": base64.StdEncoding.EncodeToString(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: secret})), "JWT_PUBLIC_KEY_B64": base64.StdEncoding.EncodeToString(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: visible}))})
	case "try-exclusive", "hold-exclusive":
		file, err := os.OpenFile(os.Getenv("DATA_RESTORE_LOCK_PATH"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0666)
		if err != nil {
			return err
		}
		defer file.Close()
		err = syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err != nil {
			if mode == "try-exclusive" && (err == syscall.EWOULDBLOCK || err == syscall.EAGAIN) {
				fmt.Println(`{"exclusive":false}`)
				return nil
			}
			return err
		}
		fmt.Println(`{"exclusive":true}`)
		if mode == "hold-exclusive" {
			<-ctx.Done()
		}
		return nil
	case "hold-business":
		query := url.Values{"host": {os.Getenv("DBHOST")}, "port": {"5432"}, "dbname": {os.Getenv("DBNAME")}, "sslmode": {"disable"}, "connect_timeout": {"5"}, "application_name": {"worker-image-barrier"}}
		dsn := url.URL{Scheme: "postgres", User: url.UserPassword(os.Getenv("DBUSER"), os.Getenv("DBPASSWORD")), RawQuery: query.Encode()}
		connection, err := pgx.Connect(ctx, dsn.String())
		if err != nil {
			return err
		}
		defer connection.Close(context.Background())
		tx, err := connection.Begin(ctx)
		if err != nil {
			return err
		}
		defer tx.Rollback(context.Background())
		if _, err = tx.Exec(ctx, `SELECT id FROM communications_communicationconfiguration WHERE id=1 FOR UPDATE`); err != nil {
			return err
		}
		fmt.Println(`{"business_locked":true}`)
		<-ctx.Done()
		bounded, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return tx.Rollback(bounded)
	}
	return fmt.Errorf("unknown fixture mode")
}

package balancer

import (
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
)

// connectionsOpened sends rounds*concurrency requests, concurrency at a time,
// and returns how many TCP connections the server accepted.
func connectionsOpened(t *testing.T, client *http.Client, concurrency, rounds int) int64 {
	t.Helper()
	var opened int64
	srv := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		w.WriteHeader(http.StatusTooManyRequests)
		io.WriteString(w, "no available endpoint")
	}))
	srv.Config.ConnState = func(_ net.Conn, s http.ConnState) {
		if s == http.StateNew {
			atomic.AddInt64(&opened, 1)
		}
	}
	srv.Start()
	defer srv.Close()

	for r := 0; r < rounds; r++ {
		var wg sync.WaitGroup
		for i := 0; i < concurrency; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				resp, err := client.Post(srv.URL+"/schedule", "application/json", nil)
				if err != nil {
					t.Errorf("post: %v", err)
					return
				}
				io.Copy(io.Discard, resp.Body)
				resp.Body.Close()
			}()
		}
		wg.Wait()
	}
	return atomic.LoadInt64(&opened)
}

// Held requests ask the scheduler again every retry interval. Those calls must
// reuse connections: one dial per call exhausts the gateway's ephemeral ports,
// the calls then fail, and the requests are placed by the round-robin fallback
// with no admission decision (EXP-173, trap G).
func TestSchedulerClient_ReusesConnections(t *testing.T) {
	const concurrency, rounds = 200, 20

	opened := connectionsOpened(t, newSchedulerHTTPClient(), concurrency, rounds)
	if opened > 2*concurrency {
		t.Fatalf("opened %d connections for %d calls at concurrency %d; want at most %d",
			opened, concurrency*rounds, concurrency, 2*concurrency)
	}

	// The client this replaced, for the record: the default Transport keeps 2
	// idle connections per host and dials for nearly every call.
	before := connectionsOpened(t, &http.Client{Timeout: scheduleRequestTimeout}, concurrency, rounds)
	t.Logf("connections for %d calls: %d with connection reuse, %d with the default Transport",
		concurrency*rounds, opened, before)
}

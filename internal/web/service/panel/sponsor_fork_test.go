package panel

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// TestForkSponsorsNeverTouchNetwork is the regression guard for this fork: if
// an upstream merge reintroduces a sponsor fetch, CI fails here rather than
// shipping an ad-bearing panel.
func TestForkSponsorsNeverTouchNetwork(t *testing.T) {
	hits := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		w.Write([]byte(`{"sponsors":[{"id":"x","slots":["login"],"until":"2099-01-01T00:00:00Z"}]}`))
	}))
	defer srv.Close()

	prev, prevBase := sponsorsURL, sponsorLogoBase
	sponsorsURL, sponsorLogoBase = srv.URL+"/sponsors.json", srv.URL+"/logos/"
	defer func() { sponsorsURL, sponsorLogoBase = prev, prevBase }()

	list, err := (&PanelService{}).GetSponsors()
	if err != nil {
		t.Fatalf("GetSponsors() error = %v", err)
	}
	if len(list.Sponsors) != 0 {
		t.Fatalf("GetSponsors() returned %d sponsors, fork must return none", len(list.Sponsors))
	}
	if hits != 0 {
		t.Fatalf("GetSponsors() made %d HTTP requests, want 0", hits)
	}
}

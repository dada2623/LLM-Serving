// This file is copied into the pinned upstream proxy package by prepare.sh.
package proxy

import (
	"context"
	"net"
	"net/http"
	"net/http/httputil"
	"os"

	"github.com/llm-d/llm-d-router/eppopt/model"
)

type eppOptRouteContextKey struct{}

type eppOptRoute struct {
	prefill, decode string
}

func (s *Server) eppOptRoute(r *http.Request, prefill string) (*http.Request, bool) {
	if os.Getenv("EPP_OPT_DYNAMIC_DECODER") != "true" {
		return r, true
	}
	values := r.Header.Values(model.DecodeHeader)
	r.Header.Del(model.DecodeHeader)
	if prefill == "" || len(values) != 1 || s.allowlistValidator == nil || !s.allowlistValidator.enabled || prefill == values[0] {
		return r, false
	}
	decode := values[0]
	host, port, err := net.SplitHostPort(decode)
	if err != nil || net.ParseIP(host) == nil || port == "" || !s.allowlistValidator.IsAllowed(decode) {
		return r, false
	}
	return r.WithContext(context.WithValue(r.Context(), eppOptRouteContextKey{}, eppOptRoute{prefill, decode})), true
}

func routeFromContext(ctx context.Context) (eppOptRoute, bool) {
	route, ok := ctx.Value(eppOptRouteContextKey{}).(eppOptRoute)
	return route, ok
}

func configureEPPOptDecode(proxy *httputil.ReverseProxy) {
	direct := proxy.Director
	proxy.Director = func(r *http.Request) {
		direct(r)
		if route, ok := routeFromContext(r.Context()); ok {
			r.URL.Host = route.decode
			r.Host = route.decode
		}
	}
	proxy.ModifyResponse = func(response *http.Response) error {
		response.Header.Del(model.ExecutedHeader)
		if route, ok := routeFromContext(response.Request.Context()); ok && response.StatusCode == http.StatusOK {
			response.Header.Set(model.ExecutedHeader, route.prefill+","+route.decode)
		}
		return nil
	}
}

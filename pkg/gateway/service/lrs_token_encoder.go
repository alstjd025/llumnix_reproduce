package service

import (
	"llumnix/pkg/gateway/tokenizer"
	"llumnix/pkg/lrs"
)

// Wire the cgo tokenizer into pkg/lrs for gateway builds. The lrs package
// itself must not import the tokenizer (and its cgo SDK) so that the
// scheduler binary can be built with CGO_ENABLED=0.
func init() {
	lrs.TokenEncoder = func(text string) (int, error) {
		tk, err := tokenizer.GetTokenizer()
		if err != nil {
			return len(text), err
		}
		tokens, err := tk.Encode(text, false)
		return len(tokens), err
	}
}

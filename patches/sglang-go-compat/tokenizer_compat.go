// Package sglang — EXP-10 compatibility shim.
//
// The public sgl-project/sglang submodule pinned by this repo does NOT
// export the Go wrappers (Tokenizer / ToolParser) that pkg/gateway uses;
// those exist only in the vendor's private SDK the stock gateway image was
// built with. The underlying C symbols, however, ARE present both in the
// prebuilt static archive extracted from the stock gateway image
// (libsgl_model_gateway_go.a) and in the submodule's Rust sources
// (src/tokenizer.rs, src/tool_parser.rs, src/memory.rs), so this file
// re-creates just the wrapper surface the gateway needs, bound to those
// documented C signatures.
//
// Deviations from the vendor wrapper (deliberate, experiment-scope):
//   - CreateTokenizerFromFileWithChatTemplate ignores the chat-template
//     argument and binds sgl_tokenizer_create_from_file. The template is
//     only consumed by chat-template rendering, which this deployment
//     never exercises (the gateway serves /v1/completions only; tokenizer
//     use is Encode/Decode for token accounting).
//   - ParseStreamIncremental passes NULL for empty tools JSON.
//
// Install: copy into lib/sglang/sgl-model-gateway/bindings/golang/
// (package sglang) before building cmd/gateway with CGO. Canonical copy
// lives at patches/sglang-go-compat/tokenizer_compat.go.
package sglang

/*
#cgo LDFLAGS: -lsgl_model_gateway_go -ldl -lm
#include <stdlib.h>
#include <stdint.h>
#include <stddef.h>

typedef struct TokenizerHandle TokenizerHandle;
typedef struct ToolParserHandle ToolParserHandle;

extern TokenizerHandle* sgl_tokenizer_create_from_file(const char* path, char** error_out);
extern int sgl_tokenizer_encode(TokenizerHandle* handle, const char* text, int add_special_tokens,
        uint32_t** token_ids_out, size_t* token_count_out, char** error_out);
extern int sgl_tokenizer_decode(TokenizerHandle* handle, const uint32_t* token_ids, size_t token_count,
        int skip_special_tokens, char** result_out, char** error_out);
extern void sgl_tokenizer_free(TokenizerHandle* handle);
extern ToolParserHandle* sgl_tool_parser_create(const char* parser_type, char** error_out);
extern int sgl_tool_parser_parse_incremental(ToolParserHandle* handle, const char* chunk,
        const char* tools_json, char** result_json_out, char** error_out);
extern void sgl_tool_parser_free(ToolParserHandle* handle);
extern void sgl_free_string(char* s);
extern void sgl_free_token_ids(uint32_t* ptr, size_t count);
*/
import "C"

import (
	"fmt"
	"runtime"
	"unsafe"
)

func takeError(errOut **C.char) string {
	if errOut == nil || *errOut == nil {
		return "unknown error"
	}
	msg := C.GoString(*errOut)
	C.sgl_free_string(*errOut)
	*errOut = nil
	return msg
}

// Tokenizer wraps the Rust FFI tokenizer handle.
type Tokenizer struct {
	h *C.TokenizerHandle
}

// CreateTokenizerFromFileWithChatTemplate loads a tokenizer from a local
// HF-format directory/file. chatTemplatePath is accepted for interface
// compatibility and ignored (see file header).
func CreateTokenizerFromFileWithChatTemplate(path, chatTemplatePath string) (*Tokenizer, error) {
	_ = chatTemplatePath
	cPath := C.CString(path)
	defer C.free(unsafe.Pointer(cPath))
	var cErr *C.char
	h := C.sgl_tokenizer_create_from_file(cPath, &cErr)
	if h == nil {
		return nil, fmt.Errorf("sgl_tokenizer_create_from_file(%s): %s", path, takeError(&cErr))
	}
	t := &Tokenizer{h: h}
	runtime.SetFinalizer(t, func(t *Tokenizer) { C.sgl_tokenizer_free(t.h) })
	return t, nil
}

// Encode tokenizes text and returns the token ids.
func (t *Tokenizer) Encode(text string, addSpecialTokens bool) ([]uint32, error) {
	cText := C.CString(text)
	defer C.free(unsafe.Pointer(cText))
	special := C.int(0)
	if addSpecialTokens {
		special = 1
	}
	var ids *C.uint32_t
	var n C.size_t
	var cErr *C.char
	rc := C.sgl_tokenizer_encode(t.h, cText, special, &ids, &n, &cErr)
	if rc != 0 {
		return nil, fmt.Errorf("sgl_tokenizer_encode rc=%d: %s", int(rc), takeError(&cErr))
	}
	defer C.sgl_free_token_ids(ids, n)
	out := make([]uint32, int(n))
	if n > 0 {
		copy(out, unsafe.Slice((*uint32)(unsafe.Pointer(ids)), int(n)))
	}
	return out, nil
}

// Decode converts token ids back to text.
func (t *Tokenizer) Decode(ids []uint32, skipSpecialTokens bool) (string, error) {
	skip := C.int(0)
	if skipSpecialTokens {
		skip = 1
	}
	var idPtr *C.uint32_t
	if len(ids) > 0 {
		idPtr = (*C.uint32_t)(unsafe.Pointer(&ids[0]))
	}
	var res *C.char
	var cErr *C.char
	rc := C.sgl_tokenizer_decode(t.h, idPtr, C.size_t(len(ids)), skip, &res, &cErr)
	if rc != 0 {
		return "", fmt.Errorf("sgl_tokenizer_decode rc=%d: %s", int(rc), takeError(&cErr))
	}
	out := C.GoString(res)
	C.sgl_free_string(res)
	return out, nil
}

// ToolParser wraps the Rust FFI streaming tool-call parser.
type ToolParser struct {
	h *C.ToolParserHandle
}

// NewToolParser creates a parser for the given parser type name.
func NewToolParser(parserType string) (*ToolParser, error) {
	cType := C.CString(parserType)
	defer C.free(unsafe.Pointer(cType))
	var cErr *C.char
	h := C.sgl_tool_parser_create(cType, &cErr)
	if h == nil {
		return nil, fmt.Errorf("sgl_tool_parser_create(%s): %s", parserType, takeError(&cErr))
	}
	p := &ToolParser{h: h}
	runtime.SetFinalizer(p, func(p *ToolParser) { C.sgl_tool_parser_free(p.h) })
	return p, nil
}

// ParseStreamIncremental feeds one streamed chunk and returns the parse
// result JSON produced by the Rust parser.
func (p *ToolParser) ParseStreamIncremental(chunk, toolsJSON string) (string, error) {
	cChunk := C.CString(chunk)
	defer C.free(unsafe.Pointer(cChunk))
	var cTools *C.char
	if toolsJSON != "" {
		cTools = C.CString(toolsJSON)
		defer C.free(unsafe.Pointer(cTools))
	}
	var res *C.char
	var cErr *C.char
	rc := C.sgl_tool_parser_parse_incremental(p.h, cChunk, cTools, &res, &cErr)
	if rc != 0 {
		return "", fmt.Errorf("sgl_tool_parser_parse_incremental rc=%d: %s", int(rc), takeError(&cErr))
	}
	out := C.GoString(res)
	C.sgl_free_string(res)
	return out, nil
}

// This file is copied into the pinned upstream EPP response handler.
package handlers

import (
	"bytes"
	"encoding/json"
)

// containsGeneratedToken ignores SSE role, finish, usage and keepalive events.
func containsGeneratedToken(lines []byte) bool {
	for line := range bytes.SplitSeq(lines, []byte{'\n'}) {
		data, ok := bytes.CutPrefix(line, []byte("data:"))
		if !ok {
			continue
		}
		var event struct {
			Choices []struct {
				Text  string
				Delta struct {
					Content          string
					Reasoning        string
					ReasoningContent string `json:"reasoning_content"`
					ToolCalls        []struct {
						Function struct{ Arguments string }
					} `json:"tool_calls"`
				}
			}
		}
		if json.Unmarshal(bytes.TrimSpace(data), &event) != nil {
			continue
		}
		for _, choice := range event.Choices {
			delta := choice.Delta
			if choice.Text != "" || delta.Content != "" || delta.Reasoning != "" || delta.ReasoningContent != "" {
				return true
			}
			for _, tool := range delta.ToolCalls {
				if tool.Function.Arguments != "" {
					return true
				}
			}
		}
	}
	return false
}

package bundle

import "embed"

// FS contains the canonical workflow and project skills installed by ltdd.
//
//go:embed all:workflows all:skills
var FS embed.FS

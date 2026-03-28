# Obsidian AI Tools: Python to Go Conversion Plan

## Executive Summary

This document outlines a comprehensive plan for converting the Obsidian AI Tools project from Python to Go. The project is an AI-powered knowledge management CLI tool for Obsidian with ~1,758 lines of source code across 20+ modules.

---

## 1. Project Overview

### Current Architecture
- **Language**: Python 3.10+
- **CLI Framework**: Typer
- **Configuration**: Pydantic Settings
- **Search**: Whoosh (full-text) + Sentence-Transformers + Annoy (semantic)
- **Data Validation**: Pydantic models
- **HTTP Client**: httpx, requests
- **Persistence**: JSON files, DuckDB

### Key Functionality
1. Content ingestion (YouTube, Web, PDF, Markdown)
2. LLM-powered note generation (OpenRouter API)
3. Full-text and semantic search
4. Concept linking (TF-IDF cosine similarity)
5. Tag hygiene analysis
6. Folder organization
7. Observability/cost tracking

---

## 2. Environment Variables

### Required Variables
| Variable | Type | Description | Go Handling |
|----------|------|-------------|-------------|
| `OPENROUTER_API_KEY` | string | API key for LLM | `os.Getenv()` + viper |
| `OBSIDIAN_VAULT_PATH` | path | Path to Obsidian vault | Validate with `os.Stat()` |
| `OBSIDIAN_INBOX_FOLDER` | string | Inbox folder name (default: "inbox") | Default in config |

### Optional Variables
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LLM_MODEL` | string | `anthropic/claude-3.5-sonnet` | OpenRouter model ID |
| `MAX_TRANSCRIPT_LENGTH` | int | 50000 | Max transcript chars |
| `YOUTUBE_API_KEY` | string | - | YouTube Data API key |
| `SUPADATA_KEY` | string | - | Supadata transcript API |
| `DECODO_API_KEY` | string | - | Decodo transcript API |
| `YOUTUBE_TRANSCRIPT_PROVIDER_ORDER` | string | `direct,supadata,decodo` | Provider fallback order |
| `EMBEDDING_MODEL` | string | `all-MiniLM-L6-v2` | Sentence transformer model |
| `CACHE_DIR` | string | `.cache` | Cache directory |
| `CACHE_TTL_HOURS` | int | 168 (7 days) | Cache time-to-live |
| `CIRCUIT_BREAKER_THRESHOLD` | int | 3 | Failures before quarantine |
| `CIRCUIT_BREAKER_TIMEOUT_HOURS` | int | 2 | Circuit breaker reset time |
| `MAX_PDF_PAGES` | int | 50 | Max PDF pages to process |
| `MAX_PDF_SIZE_MB` | int | 20 | Max PDF file size |

---

## 3. Algorithms to Implement

### 3.1 Full-Text Search (Whoosh → Bleve)

**Current Implementation**: Whoosh with TF-IDF scoring
**Go Equivalent**: [Bleve](https://blevesearch.com/)

```go
// Schema mapping
type NoteDocument struct {
    FilePath  string    `json:"file_path"`
    Title     string    `json:"title"`
    Content   string    `json:"content"`
    Tags      []string  `json:"tags"`
    Author    string    `json:"author"`
    SourceURL string    `json:"source_url"`
    Created   time.Time `json:"created"`
}

// Index creation with Bleve
func buildSearchIndex(notes []NoteDocument, indexPath string) (bleve.Index, error) {
    mapping := bleve.NewIndexMapping()

    noteMapping := bleve.NewDocumentMapping()
    noteMapping.AddFieldMappingsAt("title", bleve.NewTextFieldMapping())
    noteMapping.AddFieldMappingsAt("content", bleve.NewTextFieldMapping())
    noteMapping.AddFieldMappingsAt("tags", bleve.NewKeywordFieldMapping())

    mapping.AddDocumentMapping("note", noteMapping)

    return bleve.New(indexPath, mapping)
}
```

**Complexity**: O(log N + M) query time, O(N × avg_doc_size) space

### 3.2 Semantic Search (Sentence-Transformers + Annoy)

**Challenge**: No native Go sentence transformers

**Options**:
1. **ONNX Runtime** - Run exported ONNX model in Go
2. **External Service** - Call Python microservice or HuggingFace API
3. **go-sentence-transformers** - Limited community library
4. **Ollama embeddings** - Local embedding API

**Recommended Approach**: ONNX Runtime with pre-exported model

```go
// Using onnxruntime-go
import ort "github.com/yalue/onnxruntime_go"

type EmbeddingModel struct {
    session *ort.Session
    tokenizer *tokenizers.Tokenizer
}

func (m *EmbeddingModel) Embed(text string) ([]float32, error) {
    tokens := m.tokenizer.Encode(text)
    inputTensor := ort.NewTensor(tokens)
    outputs, err := m.session.Run(inputTensor)
    return outputs[0].GetData().([]float32), err
}
```

**ANN Index**: Annoy has Go bindings ([go-annoy](https://github.com/spotify/annoy#go))

```go
import "github.com/spotify/annoy/src/annoygo"

func buildAnnoyIndex(embeddings [][]float32, numTrees int) *annoygo.AnnoyIndex {
    dim := len(embeddings[0])
    index := annoygo.NewAnnoyIndexAngular(dim)

    for i, vec := range embeddings {
        index.AddItem(i, vec)
    }
    index.Build(numTrees)
    return index
}
```

### 3.3 Hybrid Search (Reciprocal Rank Fusion)

```go
func combineRRF(keywordResults, semanticResults []SearchResult, k float64, limit int) []SearchResult {
    scores := make(map[string]float64)

    for rank, r := range keywordResults {
        scores[r.FilePath] += 1.0 / (k + float64(rank+1))
    }
    for rank, r := range semanticResults {
        scores[r.FilePath] += 1.0 / (k + float64(rank+1))
    }

    // Sort by combined score and return top limit
    return sortByScore(scores, limit)
}
```

### 3.4 TF-IDF Concept Linking

**Current**: scikit-learn TfidfVectorizer + cosine similarity
**Go Implementation**: Custom TF-IDF or use [prose](https://github.com/jdkato/prose)

```go
type TFIDFVectorizer struct {
    vocabulary map[string]int
    idf        []float64
    maxFeatures int
    stopWords  map[string]bool
}

func (v *TFIDFVectorizer) Fit(documents []string) {
    // Build vocabulary from documents
    // Calculate IDF: log(N / df(term))
}

func (v *TFIDFVectorizer) Transform(doc string) []float64 {
    // Calculate TF-IDF vector for document
    // TF = term_count / total_terms
    // TF-IDF = TF * IDF
}

func cosineSimilarity(a, b []float64) float64 {
    var dot, normA, normB float64
    for i := range a {
        dot += a[i] * b[i]
        normA += a[i] * a[i]
        normB += b[i] * b[i]
    }
    return dot / (math.Sqrt(normA) * math.Sqrt(normB))
}
```

### 3.5 Tag Similarity (SequenceMatcher → Levenshtein)

```go
import "github.com/agnivade/levenshtein"

func calculateSimilarity(tagA, tagB string) float64 {
    a, b := strings.ToLower(tagA), strings.ToLower(tagB)
    distance := levenshtein.ComputeDistance(a, b)
    maxLen := max(len(a), len(b))
    return 1.0 - float64(distance)/float64(maxLen)
}
```

### 3.6 Jaccard Similarity (Co-occurrence)

```go
func jaccardSimilarity(setA, setB map[string]bool) float64 {
    intersection := 0
    for k := range setA {
        if setB[k] {
            intersection++
        }
    }
    union := len(setA) + len(setB) - intersection
    return float64(intersection) / float64(union)
}
```

---

## 4. Dependency Mapping

| Python Package | Go Equivalent | Notes |
|----------------|---------------|-------|
| **typer** | [cobra](https://github.com/spf13/cobra) | Industry standard CLI |
| **pydantic** | Go structs + [validator](https://github.com/go-playground/validator) | Native structs with tags |
| **pydantic-settings** | [viper](https://github.com/spf13/viper) | Config management |
| **httpx/requests** | `net/http` or [resty](https://github.com/go-resty/resty) | Native HTTP |
| **youtube-transcript-api** | Custom implementation | Call YouTube API directly |
| **openai** | [go-openai](https://github.com/sashabaranov/go-openai) | OpenAI-compatible client |
| **trafilatura** | [colly](https://github.com/gocolly/colly) + [goquery](https://github.com/PuerkitoBio/goquery) | Web scraping |
| **beautifulsoup4** | [goquery](https://github.com/PuerkitoBio/goquery) | HTML parsing |
| **pypdf** | [pdfcpu](https://github.com/pdfcpu/pdfcpu) or [unipdf](https://github.com/unidoc/unipdf) | PDF extraction |
| **Whoosh** | [bleve](https://github.com/blevesearch/bleve) | Full-text search |
| **sentence-transformers** | ONNX Runtime + exported model | See Section 3.2 |
| **annoy** | [go-annoy](https://github.com/spotify/annoy) (CGO bindings) | ANN search |
| **scikit-learn** | Custom TF-IDF implementation | See Section 3.4 |
| **python-frontmatter** | [goldmark](https://github.com/yuin/goldmark) + custom parser | Markdown parsing |
| **tenacity** | [go-retryablehttp](https://github.com/hashicorp/go-retryablehttp) | Retry logic |
| **structlog** | [zap](https://github.com/uber-go/zap) or [zerolog](https://github.com/rs/zerolog) | Structured logging |
| **duckdb** | [go-duckdb](https://github.com/marcboeker/go-duckdb) | DuckDB bindings |
| **python-dotenv** | [godotenv](https://github.com/joho/godotenv) | .env loading |

---

## 5. Project Structure (Go)

```
kai/
├── cmd/
│   └── kai/
│       └── main.go              # Entry point
├── internal/
│   ├── cli/
│   │   ├── root.go              # Root command
│   │   ├── ingest.go            # kai ingest
│   │   ├── search.go            # kai search
│   │   ├── tags.go              # kai tags
│   │   ├── connect.go           # kai connect
│   │   ├── digest.go            # kai digest
│   │   ├── preview.go           # kai preview
│   │   ├── refresh.go           # kai refresh
│   │   └── reading_list.go      # kai reading-list
│   │
│   ├── config/
│   │   └── config.go            # Viper-based configuration
│   │
│   ├── models/
│   │   ├── video.go             # VideoMetadata
│   │   ├── article.go           # ArticleMetadata
│   │   ├── note.go              # Note
│   │   └── vault.go             # VaultIndex, NoteMetadata
│   │
│   ├── providers/
│   │   ├── provider.go          # Provider interface
│   │   ├── factory.go           # ProviderFactory
│   │   ├── youtube.go           # YouTubeProvider
│   │   ├── web.go               # WebProvider
│   │   ├── pdf.go               # PDFProvider
│   │   └── file.go              # FileProvider
│   │
│   ├── youtube/
│   │   ├── client.go            # YouTube transcript client
│   │   ├── providers.go         # Direct, Supadata, Decodo
│   │   └── errors.go            # YouTube errors
│   │
│   ├── llm/
│   │   └── openrouter.go        # LLM client for note generation
│   │
│   ├── search/
│   │   ├── whoosh.go            # Bleve full-text search
│   │   ├── semantic.go          # Embedding-based search
│   │   ├── hybrid.go            # RRF combination
│   │   └── query.go             # Query parsing
│   │
│   ├── embeddings/
│   │   ├── model.go             # ONNX embedding model
│   │   ├── index.go             # Annoy index management
│   │   └── cache.go             # Embedding cache
│   │
│   ├── linking/
│   │   ├── tfidf.go             # TF-IDF vectorizer
│   │   ├── similarity.go        # Cosine similarity
│   │   └── linker.go            # ConceptLinker
│   │
│   ├── tags/
│   │   ├── hygiene.go           # Tag analysis
│   │   ├── similarity.go        # Tag similarity
│   │   └── cooccurrence.go      # Jaccard analysis
│   │
│   ├── vault/
│   │   ├── indexer.go           # Vault scanning
│   │   ├── writer.go            # Note writing
│   │   └── frontmatter.go       # Frontmatter parsing
│   │
│   ├── folder/
│   │   └── organizer.go         # Folder organization rules
│   │
│   ├── observability/
│   │   └── db.go                # DuckDB cost tracking
│   │
│   └── resilience/
│       ├── circuit_breaker.go   # Circuit breaker pattern
│       ├── retry.go             # Retry with backoff
│       └── cache.go             # File-based caching
│
├── pkg/
│   └── utils/
│       ├── rate_limiter.go
│       └── strings.go
│
├── prompts/                      # Same as Python (copy over)
│   ├── youtube_v1.md
│   ├── youtube_v2.md
│   ├── article_v1.md
│   ├── pdf_v1.md
│   └── markdown_v1.md
│
├── go.mod
├── go.sum
├── Makefile
└── README.md
```

---

## 6. Implementation Phases

### Phase 1: Foundation (Week 1-2)
1. Set up Go module structure
2. Implement configuration (viper + godotenv)
3. Create core models (VideoMetadata, ArticleMetadata, Note)
4. Set up CLI skeleton with Cobra
5. Implement structured logging (zap)

**Deliverables**:
- `kai version` command working
- Configuration loading from .env
- Basic project structure

### Phase 2: Content Providers (Week 3-4)
1. Implement Provider interface
2. YouTubeProvider with transcript fetching
3. WebProvider with colly/goquery
4. PDFProvider with pdfcpu
5. FileProvider for local markdown
6. ProviderFactory for source detection

**Deliverables**:
- `kai preview <url>` working
- All provider types functional

### Phase 3: LLM Integration (Week 5)
1. OpenRouter client using go-openai
2. Prompt template loading
3. Note generation with structured output parsing
4. Cost tracking integration

**Deliverables**:
- `kai ingest <url>` working end-to-end
- Notes written to vault

### Phase 4: Vault Operations (Week 6)
1. Vault indexer (scan .md files, parse frontmatter)
2. VaultIndex persistence (JSON)
3. Folder organizer with scoring algorithm
4. `kai process-inbox` command

**Deliverables**:
- `kai rebuild-index` working
- `kai process-inbox` working

### Phase 5: Full-Text Search (Week 7)
1. Bleve index creation
2. Query parsing (keywords, tags, dates)
3. Search result formatting
4. `kai search` command

**Deliverables**:
- `kai search --keyword "AI" --tag llm` working

### Phase 6: Semantic Search (Week 8-9)
1. ONNX runtime integration for embeddings
2. Export all-MiniLM-L6-v2 to ONNX format
3. Annoy index with Go bindings
4. Embedding caching
5. Hybrid search (RRF)

**Deliverables**:
- `kai search --semantic "machine learning"` working
- `kai search --hybrid` working

### Phase 7: Concept Linking (Week 10)
1. TF-IDF vectorizer implementation
2. Cosine similarity computation
3. Keyword extraction
4. Orphan note detection
5. Wikilink insertion

**Deliverables**:
- `kai connect --folder "AI"` working
- `kai connect --auto-link` working

### Phase 8: Tag Hygiene (Week 11)
1. Tag similarity (Levenshtein)
2. Co-occurrence analysis (Jaccard)
3. Orphan tag detection
4. Interactive fix workflow

**Deliverables**:
- `kai tags` working
- `kai list-tags` working

### Phase 9: Advanced Features (Week 12)
1. Digest generation
2. Reading list management
3. Refresh command
4. Stats and quality commands

**Deliverables**:
- All remaining commands functional

### Phase 10: Polish & Testing (Week 13-14)
1. Comprehensive test suite
2. Error handling refinement
3. Performance optimization
4. Documentation
5. CI/CD setup

**Deliverables**:
- 80%+ test coverage
- Production-ready binary

---

## 7. Technical Challenges & Solutions

### Challenge 1: Sentence Transformers in Go
**Problem**: No native Go library for sentence embeddings
**Solutions**:
1. **ONNX Export** (Recommended)
   - Export Python model: `model.save('model.onnx')`
   - Use [onnxruntime-go](https://github.com/yalue/onnxruntime_go)
   - Bundle ONNX file with binary or download on first run

2. **External API**
   - Use HuggingFace Inference API
   - Or run local Ollama with embedding model

3. **Sidecar Service**
   - Small Python microservice just for embeddings
   - Communicate via gRPC or HTTP

### Challenge 2: Annoy CGO Bindings
**Problem**: Annoy requires CGO, complicates cross-compilation
**Solutions**:
1. Use official Go bindings (requires CGO)
2. Alternative: [hnswlib-go](https://github.com/Bithack/go-hnsw) - pure Go
3. Alternative: [faiss-go](https://github.com/DataIntelligenceCrew/go-faiss)

### Challenge 3: Trafilatura Equivalent
**Problem**: No exact Go equivalent for trafilatura's quality extraction
**Solutions**:
1. Combine colly + goquery + custom heuristics
2. Use [go-readability](https://github.com/go-shiori/go-readability)
3. Call readability.js via embedded V8

### Challenge 4: YouTube Transcript API
**Problem**: youtube-transcript-api uses unofficial YouTube API
**Solutions**:
1. Port the Python logic (parse innertube responses)
2. Use paid APIs (Supadata, Decodo) as primary
3. Consider yt-dlp as subprocess (last resort)

---

## 8. Go-Specific Improvements

### Concurrency
- Use goroutines for parallel transcript fetching
- Concurrent embedding computation
- Parallel vault scanning with worker pools

```go
func indexVaultConcurrent(vaultPath string) (*VaultIndex, error) {
    files := make(chan string, 100)
    results := make(chan *NoteMetadata, 100)

    // Start workers
    var wg sync.WaitGroup
    for i := 0; i < runtime.NumCPU(); i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for file := range files {
                note, _ := parseNote(file)
                results <- note
            }
        }()
    }

    // Walk directory and send files
    go func() {
        filepath.Walk(vaultPath, func(path string, info os.FileInfo, err error) error {
            if strings.HasSuffix(path, ".md") {
                files <- path
            }
            return nil
        })
        close(files)
    }()

    // Collect results
    go func() {
        wg.Wait()
        close(results)
    }()

    var notes []*NoteMetadata
    for note := range results {
        notes = append(notes, note)
    }
    return &VaultIndex{Notes: notes}, nil
}
```

### Single Binary Distribution
- Embed prompts using `//go:embed`
- Embed ONNX model (optional, ~90MB)
- Cross-compile for all platforms

```go
//go:embed prompts/*.md
var promptsFS embed.FS

func loadPrompt(version string) (string, error) {
    return promptsFS.ReadFile(fmt.Sprintf("prompts/%s.md", version))
}
```

### Error Handling
- Use custom error types with context
- Wrap errors with `fmt.Errorf("...: %w", err)`

```go
type TranscriptError struct {
    VideoID  string
    Provider string
    Err      error
}

func (e *TranscriptError) Error() string {
    return fmt.Sprintf("transcript fetch failed for %s via %s: %v",
        e.VideoID, e.Provider, e.Err)
}
```

---

## 9. Testing Strategy

### Unit Tests
- Table-driven tests for algorithms
- Mock interfaces for external services
- Use testify/assert for assertions

```go
func TestCosineSimilarity(t *testing.T) {
    tests := []struct {
        name     string
        a, b     []float64
        expected float64
    }{
        {"identical", []float64{1, 0, 1}, []float64{1, 0, 1}, 1.0},
        {"orthogonal", []float64{1, 0, 0}, []float64{0, 1, 0}, 0.0},
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            result := cosineSimilarity(tt.a, tt.b)
            assert.InDelta(t, tt.expected, result, 0.0001)
        })
    }
}
```

### Integration Tests
- Use testcontainers for DuckDB
- Temporary directories for vault tests
- HTTP mocks for API tests

### Benchmarks
- Benchmark search algorithms
- Benchmark embedding generation
- Profile memory usage

```go
func BenchmarkTFIDFTransform(b *testing.B) {
    vectorizer := NewTFIDFVectorizer(5000)
    vectorizer.Fit(testDocuments)

    b.ResetTimer()
    for i := 0; i < b.N; i++ {
        vectorizer.Transform(testDocuments[i%len(testDocuments)])
    }
}
```

---

## 10. Migration Checklist

### Pre-Migration
- [ ] Export ONNX model from sentence-transformers
- [ ] Document all edge cases in Python tests
- [ ] Freeze Python feature development
- [ ] Set up Go development environment

### During Migration
- [ ] Phase 1: Foundation complete
- [ ] Phase 2: Providers complete
- [ ] Phase 3: LLM integration complete
- [ ] Phase 4: Vault operations complete
- [ ] Phase 5: Full-text search complete
- [ ] Phase 6: Semantic search complete
- [ ] Phase 7: Concept linking complete
- [ ] Phase 8: Tag hygiene complete
- [ ] Phase 9: Advanced features complete
- [ ] Phase 10: Testing & polish complete

### Post-Migration
- [ ] Parallel testing (Python vs Go outputs)
- [ ] Performance comparison benchmarks
- [ ] User acceptance testing
- [ ] Documentation update
- [ ] Release binaries for all platforms
- [ ] Deprecate Python version

---

## 11. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| ONNX embedding quality differs | Medium | High | Validate outputs against Python |
| Annoy CGO breaks on ARM | Medium | Medium | Test on Apple Silicon, have fallback |
| Web extraction quality lower | High | Medium | Test against trafilatura, tune heuristics |
| YouTube transcript API changes | Medium | High | Prioritize paid APIs, add monitoring |
| Performance regression | Low | Medium | Benchmark critical paths |
| Test coverage gaps | Medium | Medium | Port Python tests systematically |

---

## 12. Success Metrics

1. **Feature Parity**: All 14 CLI commands functional
2. **Performance**: 2x faster search, 50% smaller binary than Python+deps
3. **Test Coverage**: 80%+ code coverage
4. **Cross-Platform**: Binaries for Linux, macOS, Windows (amd64 + arm64)
5. **Search Quality**: RRF scores match Python implementation ±5%
6. **Embedding Quality**: Cosine similarity with Python embeddings >0.99

---

## Appendix A: Full go.mod Dependencies

```go
module github.com/user/kai

go 1.22

require (
    github.com/spf13/cobra v1.8.0           // CLI
    github.com/spf13/viper v1.18.0          // Config
    github.com/joho/godotenv v1.5.1         // .env loading
    github.com/go-playground/validator/v10 v10.18.0  // Validation

    github.com/sashabaranov/go-openai v1.20.0  // OpenAI client
    github.com/gocolly/colly/v2 v2.1.0       // Web scraping
    github.com/PuerkitoBio/goquery v1.8.1    // HTML parsing
    github.com/pdfcpu/pdfcpu v0.6.0          // PDF extraction

    github.com/blevesearch/bleve/v2 v2.3.10  // Full-text search
    github.com/spotify/annoy v0.0.0          // ANN search (CGO)
    github.com/yalue/onnxruntime_go v1.4.0   // ONNX runtime

    github.com/marcboeker/go-duckdb v1.5.6   // DuckDB
    github.com/yuin/goldmark v1.7.0          // Markdown parsing

    github.com/uber-go/zap v1.27.0           // Logging
    github.com/hashicorp/go-retryablehttp v0.7.5  // Retry
    github.com/agnivade/levenshtein v1.1.1   // String similarity

    github.com/stretchr/testify v1.9.0       // Testing
)
```

---

## Appendix B: CLI Command Reference

| Command | Status | Priority |
|---------|--------|----------|
| `kai ingest <url>` | Phase 3 | P0 |
| `kai search` | Phase 5-6 | P0 |
| `kai rebuild-index` | Phase 4 | P0 |
| `kai list-tags` | Phase 8 | P1 |
| `kai process-inbox` | Phase 4 | P1 |
| `kai connect` | Phase 7 | P1 |
| `kai tags` | Phase 8 | P1 |
| `kai preview` | Phase 2 | P2 |
| `kai stats` | Phase 9 | P2 |
| `kai quality` | Phase 9 | P2 |
| `kai digest` | Phase 9 | P2 |
| `kai refresh` | Phase 9 | P2 |
| `kai reading-list` | Phase 9 | P2 |
| `kai version` | Phase 1 | P0 |

---

*Document Version: 1.0*
*Created: 2026-01-28*
*Estimated Duration: 14 weeks*

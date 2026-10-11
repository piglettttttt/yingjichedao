/*
 * Emergency-lane plate evidence - manual review GUI.
 * Pure Win32 API. No Python / no third-party runtime.
 *
 * Visual style: De Stijl / Neoplasticism (Mondrian).
 *   - the whole surface is divided by THICK BLACK lines (about 1.5% of the
 *     window width) into rectangles of unequal size
 *   - lines are strictly horizontal or vertical: no diagonals, no curves,
 *     no rounded corners
 *   - palette is limited to red #D0302A, yellow #EBC226, blue #1D3F8C,
 *     black, off-white #F4F1EA and a light grey
 *   - everything is FLAT: no gradients, shadows or textures
 *   - asymmetric but balanced: mostly off-white, colour in only one or two
 *     cells, held up by the size and placement of the blocks
 *   - text is short, rare, bold and geometric, sitting inside white cells
 *
 * Because of that, the screen is drawn entirely by hand: no stock BUTTON or
 * STATIC controls (their themed 3D borders cannot be flattened into the grid).
 * The one exception is the editable "user confirm" field, which keeps a real
 * EDIT control so the caret and IME work, but that control is subclassed to
 * paint a flat off-white face with a thick black frame.
 *
 * Flow:
 *   start -> ask for "initial result folder" -> validate (must not contain CJK)
 *         -> review screen:
 *              Detected plate / Confidence : read-only
 *              User confirm                : default = detected, editable
 *              Report as: Yes / No / Skip
 *              Prev / Goto unconfirmed / Finish report
 *
 * Plate format: 1 CJK char + 6 or 7 of [A-Z0-9]
 *
 * Build: cl /nologo /O2 /W3 /DUNICODE /D_UNICODE /EHsc PlateReview.cpp
 *            /link /SUBSYSTEM:WINDOWS user32.lib gdi32.lib gdiplus.lib
 *                  comdlg32.lib shell32.lib ole32.lib
 */

#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commdlg.h>
#include <shlobj.h>
/*
 * NOTE: objbase.h MUST come before gdiplus.h.
 * With WIN32_LEAN_AND_MEAN, windows.h does not pull in the OLE headers, so
 * MIDL_INTERFACE (used by GdiplusImaging.h) stays undefined and gdiplus.h
 * fails with "syntax error: identifier 'Gdiplus'". Verified by minimal repro.
 */
#include <objbase.h>
#include <gdiplus.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>


#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "gdiplus.lib")
#pragma comment(lib, "comdlg32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "ole32.lib")

#define ARRAYSZ(a) (sizeof(a) / sizeof((a)[0]))
#define MAXC 512
#define MAXCOL 24
#define STR 512

/* ---------------- Minimalist Flat palette ----------------
 *
 * Pure solids only, high contrast, no greys for surfaces:
 *   black  - text, borders, filled buttons
 *   white  - page background, cards, input faces
 *   accent - focus underline, hover text, a few emphasis blocks
 * All corners are square; there are no shadows and no gradients anywhere.
 */
static const COLORREF MF_BLACK = RGB(0x00, 0x00, 0x00);
static const COLORREF MF_WHITE = RGB(0xFF, 0xFF, 0xFF);
static const COLORREF MF_ACCENT = RGB(0xFF, 0x33, 0x66);
/* the only grey allowed: deliberately de-emphasised helper text */
static const COLORREF MF_MUTED = RGB(0x6B, 0x6B, 0x6B);
/* neutral fill for the skip state */
static const COLORREF MF_NEUTRAL = RGB(0xE8, 0xE8, 0xE8);
/* pressed-state flash, a deliberately short-lived light tone */
static const COLORREF MF_PRESSED = RGB(0xEB, 0xEB, 0xEB);

enum { ST_PENDING = 0, ST_YES, ST_NO, ST_SKIP };

typedef struct {
    wchar_t detected[STR];
    wchar_t conf[STR];
    wchar_t ts[STR];
    wchar_t tsEnd[STR];
    wchar_t dur[STR];
    wchar_t frames[STR];
    wchar_t image[STR];
    wchar_t overlap[STR];
    wchar_t user[STR];
    /*
     * Evidence materials, all optional (a CSV with these columns missing still
     * loads and simply shows fewer frames). The first step prepares them; this
     * program only copies what the operator confirmed:
     *   evDir      - per-case folder under the evidence-images folder that holds
     *                the three frames
     *   evName[3]  - the three fixed file names inside it
     *                (highest-confidence frame / earliest-nearby / latest-nearby)
     *   evSec[2]   - in-video time of the 2nd and 3rd frame, shown under them
     */
    wchar_t evDir[STR];
    wchar_t evName[3][STR];
    wchar_t evSec[2][STR];
    /* filled while the report is written */
    int evCopied[3];
    int st;
} Case;

/* ---------------- globals ---------------- */

static HINSTANCE g_inst;
static HWND g_hwnd;
static HFONT g_fSmall, g_fBody, g_fLabel, g_fValue, g_fBig, g_fTitle;
static HFONT g_fHero, g_fField;

static Case g_cases[MAXC];
static int g_n, g_idx;
static wchar_t g_folder[MAX_PATH * 2];
static int g_nEvCopied;   /* evidence-frame copies for the done box */

/* screen: 0 = entry, 1 = review, 2 = "violation details" prompt */
static int g_screen = 0;
static int g_wherePrev = 1;          /* screen to return to when the dialog closes */
static wchar_t g_where[512];         /* location + heading, up to 200 chars */
static int g_whereErr = 0;

/* entry screen state */
static wchar_t g_entry[MAX_PATH * 2];
static int g_entryCaret = 0;
static wchar_t g_entryErr[256];

/* review screen: editable fields (kept in sync with their EDIT controls) */
static HWND g_edit = NULL;      /* user-confirm plate */
static HWND g_editDet = NULL;   /* detected plate (read-only display) */
static HWND g_editEntry = NULL; /* entry screen: folder path (paste-friendly) */
static WNDPROC g_editOld = NULL;      /* original proc of the confirm field */
static WNDPROC g_editDetOld = NULL;   /* original proc of the detected field */
static WNDPROC g_editEntryOld = NULL; /* original proc of the folder field */
static HWND g_editWhere = NULL;       /* violation place + heading (multiline) */
static WNDPROC g_editWhereOld = NULL;

/* image */
static Gdiplus::GdiplusStartupInput g_gdiIn;
static ULONG_PTR g_gdiTok;
static Gdiplus::Image *g_img = NULL;
static HBITMAP g_bmp = NULL;
static wchar_t g_path[MAX_PATH * 2];
static int g_iw, g_ih;          /* source size */
static int g_basew, g_baseh;    /* fitted base bitmap size */
static int g_zoom = 100;        /* 100 = fit; up to 800 */
static int g_panx, g_pany;
static int g_drag = 0;
static POINT g_dragPt;

/* layout metrics, recomputed on resize */
static int g_W, g_H;
static int g_line;              /* black line thickness */

/* dpi */
static int g_dpi = 96;
static int SC(int v) { return MulDiv(v, g_dpi, 96); }

/* forward decls */
typedef struct { int x, y, w, h; } Rct;
static Rct g_rImgCard;      /* image card rect; filled by layout_review */
static void show_case(void);
static void layout_review(void);
static void layout_entry(void);
static void refit_image(void);
static void load_image(const wchar_t *path, int cw, int chh);
static void paint_review(HDC dc);
static void paint_entry(HDC dc);
static void sync_user_from_edit(void);
static void sync_detected_from_edit(void);
static void sync_entry_from_edit(void);
static void sync_where_from_edit(void);
static void place_edits(void);
static void layout_where(void);
static void open_where_dlg(void);
static void write_report_now(void);

/* ---------------- helpers ---------------- */

static BOOL has_cjk(const wchar_t *s)
{
    for (; s && *s; ++s)
        if (*s >= 0x4E00 && *s <= 0x9FFF) return TRUE;
    return FALSE;
}

static void trim(wchar_t *s)
{
    if (!s) return;
    wchar_t *p = s;
    while (*p == L' ' || *p == L'\t' || *p == L'\r' || *p == L'\n') ++p;
    if (p != s) memmove(s, p, (wcslen(p) + 1) * sizeof(wchar_t));
    size_t n = wcslen(s);
    while (n > 0) {
        wchar_t c = s[n - 1];
        if (c == L' ' || c == L'\t' || c == L'\r' || c == L'\n' || c == L'"') s[--n] = 0;
        else break;
    }
}

/* plate format: 1 CJK char + 6 or 7 of [A-Z0-9] */
static BOOL plate_ok(const wchar_t *s)
{
    if (!s || !*s) return FALSE;
    if (!(*s >= 0x4E00 && *s <= 0x9FFF)) return FALSE;
    int n = 0;
    for (const wchar_t *p = s + 1; *p; ++p, ++n) {
        wchar_t c = *p;
        if (!((c >= L'A' && c <= L'Z') || (c >= L'0' && c <= L'9'))) return FALSE;
    }
    return (n == 6 || n == 7);
}

static void to_upper_plate(wchar_t *s)
{
    for (; s && *s; ++s)
        if (*s >= L'a' && *s <= L'z') *s = (wchar_t)(*s - L'a' + L'A');
}

static void join_path(wchar_t *dst, const wchar_t *dir, const wchar_t *name)
{
    size_t n = wcslen(dir);
    int need = (n > 0 && dir[n - 1] != L'\\' && dir[n - 1] != L'/');
    _snwprintf(dst, MAX_PATH * 2 - 1, L"%s%s%s", dir, need ? L"\\" : L"", name);
}

static int file_exists(const wchar_t *p)
{
    DWORD a = GetFileAttributesW(p);
    return (a != INVALID_FILE_ATTRIBUTES) && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static int is_dir(const wchar_t *p)
{
    DWORD a = GetFileAttributesW(p);
    return (a != INVALID_FILE_ATTRIBUTES) && (a & FILE_ATTRIBUTE_DIRECTORY);
}

static void utf8_to_w(const char *src, wchar_t *dst, int cch)
{
    if (cch <= 0) return;
    int r = MultiByteToWideChar(CP_UTF8, 0, src, -1, dst, cch - 1);
    if (r <= 0) { dst[0] = 0; return; }
    dst[r] = 0;
}

/* ---------------- flat drawing primitives ---------------- */

static void fill_rect(HDC dc, int x, int y, int w, int h, COLORREF col)
{
    RECT r = { x, y, x + w, y + h };
    HBRUSH b = CreateSolidBrush(col);
    FillRect(dc, &r, b);
    DeleteObject(b);
}

/*
 * Minimalist Flat geometry.
 *
 * This style has no grid lines: the only strokes used are the 2px black
 * outline of "cards" (the image cell, buttons, the status chip) and the 2px
 * black underline of inputs. Corners are always square.
 */
#define MF_STROKE 2

static void frame_2px(HDC dc, int x, int y, int w, int h, COLORREF col)
{
    fill_rect(dc, x, y, w, MF_STROKE, col);
    fill_rect(dc, x, y + h - MF_STROKE, w, MF_STROKE, col);
    fill_rect(dc, x, y, MF_STROKE, h, col);
    fill_rect(dc, x + w - MF_STROKE, y, MF_STROKE, h, col);
}

/* input affordance: no box, just a 2px rule under the field */
static void underline(HDC dc, int x, int y, int w, COLORREF col)
{
    fill_rect(dc, x, y, w, MF_STROKE, col);
}

static void draw_text(HDC dc, const wchar_t *s, int x, int y, int w, int h,
                      HFONT f, COLORREF col, UINT fmt)
{
    if (!s || !*s) return;
    RECT r = { x, y, x + w, y + h };
    HGDIOBJ of = SelectObject(dc, f);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, col);
    DrawTextW(dc, s, -1, &r, fmt | DT_NOCLIP);
    SelectObject(dc, of);
}

static void draw_text_ml(HDC dc, const wchar_t *s, int x, int y, int w, int h,
                         HFONT f, COLORREF col, UINT fmt)
{
    if (!s || !*s) return;
    RECT r = { x, y, x + w, y + h };
    HGDIOBJ of = SelectObject(dc, f);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, col);
    DrawTextW(dc, s, -1, &r, fmt | DT_WORDBREAK | DT_NOCLIP);
    SelectObject(dc, of);
}

/* ---------------- block letters (rectangles only, no font) ----------------
 *
 * 5x7 bitmap glyphs built from filled squares, so the title is literally
 * "letters made of rectangles". Only what the screens need is provided.
 */
static const char *glyph5x7(wchar_t ch)
{
    switch (ch) {
    case L'0': return "01110" "10001" "10011" "10101" "11001" "10001" "01110";
    case L'1': return "00100" "01100" "00100" "00100" "00100" "00100" "01110";
    case L'2': return "01110" "10001" "00001" "00010" "00100" "01000" "11111";
    case L'3': return "11111" "00010" "00100" "00010" "00001" "10001" "01110";
    case L'4': return "00010" "00110" "01010" "10010" "11111" "00010" "00010";
    case L'5': return "11111" "10000" "11110" "00001" "00001" "10001" "01110";
    case L'6': return "00110" "01000" "10000" "11110" "10001" "10001" "01110";
    case L'7': return "11111" "00001" "00010" "00100" "01000" "01000" "01000";
    case L'8': return "01110" "10001" "10001" "01110" "10001" "10001" "01110";
    case L'9': return "01110" "10001" "10001" "01111" "00001" "00010" "01100";
    case L'A': return "01110" "10001" "10001" "11111" "10001" "10001" "10001";
    }
    return NULL;
}

/* Draw a string with block glyphs where available, system font otherwise.
 * Returns the x advance. */
static int draw_block_text(HDC dc, const wchar_t *s, int x, int y, int cell,
                           COLORREF col, HFONT fallback, COLORREF fbcol)
{
    int cx = x;
    for (const wchar_t *p = s; *p; ++p) {
        const char *gl = glyph5x7(*p);
        if (gl) {
            for (int r = 0; r < 7; ++r)
                for (int c = 0; c < 5; ++c)
                    if (gl[r * 5 + c] == '1')
                        fill_rect(dc, cx + c * cell, y + r * cell, cell, cell, col);
            cx += 6 * cell;
        } else {
            /* fall back to the geometric sans for CJK / other glyphs */
            wchar_t one[2] = { *p, 0 };
            int w = cell * 6;
            draw_text(dc, one, cx, y - cell, w, cell * 9, fallback, fbcol,
                      DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            cx += w;
        }
    }
    return cx - x;
}

/* ---------------- image ---------------- */

static void free_image(void)
{
    if (g_img) { delete g_img; g_img = NULL; }
    if (g_bmp) { DeleteObject(g_bmp); g_bmp = NULL; }
    g_iw = g_ih = g_basew = g_baseh = 0;
    g_zoom = 100;
    g_panx = g_pany = 0;
    g_drag = 0;
}

static void reset_zoom(void) { g_zoom = 100; g_panx = g_pany = 0; }

/* Two-stage decode: downsample first, then scale to the exact target size. */
static void load_image(const wchar_t *path, int cw, int chh)
{
    free_image();
    if (!path || !*path || !file_exists(path)) { g_path[0] = 0; return; }
    if (cw <= 0 || chh <= 0) return;
    wcsncpy(g_path, path, MAX_PATH * 2 - 1);
    g_path[MAX_PATH * 2 - 1] = 0;

    g_img = Gdiplus::Image::FromFile(path);
    if (!g_img || g_img->GetLastStatus() != Gdiplus::Ok) { free_image(); return; }

    UINT iw = g_img->GetWidth(), ih = g_img->GetHeight();
    if (iw == 0 || ih == 0) { free_image(); return; }

    double sx = (double)cw / iw, sy = (double)chh / ih;
    double s = sx < sy ? sx : sy;
    int dw = (int)(iw * s), dh = (int)(ih * s);
    if (dw < 1) dw = 1;
    if (dh < 1) dh = 1;
    if (dw > cw) dw = cw;
    if (dh > chh) dh = chh;

    Gdiplus::Bitmap *mid = NULL;
    if (s < 0.5) {
        Gdiplus::Bitmap *half = new Gdiplus::Bitmap(
            (INT)(iw * s + 0.5), (INT)(ih * s + 0.5), PixelFormat32bppARGB);
        if (half->GetLastStatus() == Gdiplus::Ok) {
            Gdiplus::Graphics gs(half);
            gs.SetInterpolationMode(Gdiplus::InterpolationModeHighQualityBicubic);
            gs.DrawImage(g_img, 0, 0, half->GetWidth(), half->GetHeight());
            mid = half;
        } else {
            delete half;
        }
    }

    Gdiplus::Bitmap *bmp = new Gdiplus::Bitmap(dw, dh, PixelFormat32bppARGB);
    Gdiplus::Graphics g(bmp);
    g.SetInterpolationMode(Gdiplus::InterpolationModeHighQualityBicubic);
    g.SetPixelOffsetMode(Gdiplus::PixelOffsetModeHighQuality);
    if (mid) {
        g.DrawImage(mid, 0, 0, dw, dh);
        delete mid;
    } else {
        g.DrawImage(g_img, 0, 0, dw, dh);
    }
    HBITMAP hb = NULL;
    bmp->GetHBITMAP(Gdiplus::Color(255, 255, 255), &hb);
    delete bmp;

    g_bmp = hb;
    g_iw = (int)iw;
    g_ih = (int)ih;
    g_basew = dw;
    g_baseh = dh;
    reset_zoom();
}

/* Re-fit the current image into the image card's inner area (the card has a
 * 16px inset plus the 2px outline). Keeps the same source file, so a resize
 * only costs the scaling itself. */
static void refit_image(void)
{
    if (!g_path[0]) return;
    int iw = g_rImgCard.w - SC(32);
    int ih = g_rImgCard.h - SC(32);
    if (iw < 40 || ih < 40) return;
    load_image(g_path, iw, ih);
}

/* ---------------- font setup ---------------- */

static HFONT mk_font(int pt, int weight)
{
    return CreateFontW(-SC(pt), 0, 0, 0, weight, 0, 0, 0, DEFAULT_CHARSET,
                       0, 0, CLEARTYPE_QUALITY, 0, L"Bahnschrift SemiBold");
}

/*
 * Type scale for Minimalist Flat: one bold geometric sans, used at a small
 * number of sizes. Headlines are bold with tight spacing; labels are small and
 * bold (the "uppercase tracked" treatment is approximated by tight tracking
 * plus a smaller size, since GDI has no letter-spacing knob).
 */
static void init_fonts(void)
{
    g_fSmall = mk_font(13, FW_SEMIBOLD);   /* helper text, de-emphasised */
    g_fBody = mk_font(17, FW_MEDIUM);      /* body copy, buttons */
    g_fLabel = mk_font(12, FW_BOLD);       /* field labels */
    g_fValue = mk_font(24, FW_BOLD);       /* legacy value size */
    g_fBig = mk_font(20, FW_BOLD);
    g_fTitle = mk_font(32, FW_BOLD);       /* screen title */
    g_fHero = mk_font(64, FW_BOLD);        /* entry headline */
    g_fField = mk_font(22, FW_SEMIBOLD);   /* plate text inside inputs */
}

/* ---------------- CSV (UTF-8) ---------------- */

static int split_csv(const wchar_t *line, wchar_t cols[][STR], int maxcol)
{
    int n = 0;
    const wchar_t *p = line;
    while (n < maxcol) {
        int k = 0;
        if (*p == L'"') {
            ++p;
            while (*p) {
                if (*p == L'"') {
                    if (p[1] == L'"') { if (k < STR - 1) cols[n][k++] = L'"'; p += 2; continue; }
                    ++p; break;
                }
                if (k < STR - 1) cols[n][k++] = *p;
                ++p;
            }
        } else {
            while (*p && *p != L',') { if (k < STR - 1) cols[n][k++] = *p; ++p; }
        }
        cols[n][k] = 0;
        trim(cols[n]);
        ++n;
        if (*p == L',') { ++p; continue; }
        break;
    }
    return n;
}

static int find_col(wchar_t hdr[][STR], int nh, const wchar_t *name)
{
    for (int i = 0; i < nh; ++i)
        if (wcscmp(hdr[i], name) == 0) return i;
    return -1;
}

/*
 * Column names, all as \u escapes.
 *   plate / confidence / time_start / time_end
 *   duration_sec / frames / evidence_image / emergency_lane_overlap
 * time_start is generic: dashcam clock when the video has an LCD overlay,
 * otherwise the in-video playback time (see make_outputs.py --time-mode).
 */
static const wchar_t *COL_PLATE = L"\u8F66\u724C\u53F7";
static const wchar_t *COL_CONF = L"\u8F66\u724C\u7F6E\u4FE1\u5EA6";
static const wchar_t *COL_TS = L"\u65F6\u95F4_\u8D77";
static const wchar_t *COL_END = L"\u65F6\u95F4_\u6B62";
static const wchar_t *COL_DUR = L"\u51FA\u73B0\u65F6\u957F_\u79D2";
static const wchar_t *COL_FR = L"\u5E27\u6570";
static const wchar_t *COL_IMG = L"\u8BC1\u636E\u56FE";
static const wchar_t *COL_OV = L"\u5E94\u6025\u8F66\u9053\u91CD\u53E0";
/*
 * v0.3.0 - evidence materials prepared by the first step. Optional: read by
 * NAME, so a CSV produced before this (or one missing a column) still loads and
 * simply shows fewer evidence frames.
 *   COL_IMGDIR - per-case folder under the evidence-images folder
 *   COL_IMG1/2/3 - the three fixed file names inside that folder
 */
static const wchar_t *COL_IMGDIR = L"\u8BC1\u636E\u76EE\u5F55";
static const wchar_t *COL_IMG1 = L"\u8BC1\u636E\u56FE1";
static const wchar_t *COL_IMG2 = L"\u8BC1\u636E\u56FE2";
static const wchar_t *COL_FR2 = L"\u8BC1\u636E\u56FE2_\u65F6\u523B_\u79D2";
static const wchar_t *COL_IMG3 = L"\u8BC1\u636E\u56FE3";
static const wchar_t *COL_FR3 = L"\u8BC1\u636E\u56FE3_\u65F6\u523B_\u79D2";

static int load_cases(const wchar_t *folder)
{
    wchar_t path[MAX_PATH * 2];
    join_path(path, folder, L"\u68C0\u6D4B\u7ED3\u679C.csv");
    if (!file_exists(path)) return 0;

    FILE *fp = _wfopen(path, L"rb");
    if (!fp) return 0;
    fseek(fp, 0, SEEK_END);
    long sz = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    if (sz <= 0) { fclose(fp); return 0; }
    char *buf = (char *)malloc((size_t)sz + 1);
    if (!buf) { fclose(fp); return 0; }
    size_t rd = fread(buf, 1, (size_t)sz, fp);
    buf[rd] = 0;
    fclose(fp);

    char *start = buf;
    if (rd >= 3 && (unsigned char)buf[0] == 0xEF && (unsigned char)buf[1] == 0xBB &&
        (unsigned char)buf[2] == 0xBF) start = buf + 3;

    wchar_t wline[8192];
    wchar_t hdr[MAXCOL][STR];
    int nh = -1, in_det = -1, in_conf = -1, in_ts = -1, in_end = -1;
    int in_dur = -1, in_fr = -1, in_img = -1, in_ov = -1;
    int in_imgdir = -1, in_img1 = -1, in_img2 = -1, in_fr2 = -1;
    int in_img3 = -1, in_fr3 = -1;
    g_n = 0;

    char *line = start;
    while (line && *line) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = 0;
        size_t ll = strlen(line);
        if (ll && line[ll - 1] == '\r') line[ll - 1] = 0;

        if (*line) {
            utf8_to_w(line, wline, (int)ARRAYSZ(wline));
            wchar_t cols[MAXCOL][STR];
            int nc = split_csv(wline, cols, MAXCOL);
            if (nh < 0) {
                nh = nc;
                for (int i = 0; i < nc; ++i) wcsncpy(hdr[i], cols[i], STR - 1);
                in_det = find_col(hdr, nh, COL_PLATE);
                in_conf = find_col(hdr, nh, COL_CONF);
                in_ts = find_col(hdr, nh, COL_TS);
                in_end = find_col(hdr, nh, COL_END);
                in_dur = find_col(hdr, nh, COL_DUR);
                in_fr = find_col(hdr, nh, COL_FR);
                in_img = find_col(hdr, nh, COL_IMG);
                in_ov = find_col(hdr, nh, COL_OV);
                in_imgdir = find_col(hdr, nh, COL_IMGDIR);
                in_img1 = find_col(hdr, nh, COL_IMG1);
                in_img2 = find_col(hdr, nh, COL_IMG2);
                in_fr2 = find_col(hdr, nh, COL_FR2);
                in_img3 = find_col(hdr, nh, COL_IMG3);
                in_fr3 = find_col(hdr, nh, COL_FR3);
            } else if (g_n < MAXC) {
                Case *c = &g_cases[g_n];
                memset(c, 0, sizeof(*c));
                if (in_det >= 0 && in_det < nc) wcsncpy(c->detected, cols[in_det], STR - 1);
                if (in_conf >= 0 && in_conf < nc) wcsncpy(c->conf, cols[in_conf], STR - 1);
                if (in_ts >= 0 && in_ts < nc) wcsncpy(c->ts, cols[in_ts], STR - 1);
                if (in_end >= 0 && in_end < nc) wcsncpy(c->tsEnd, cols[in_end], STR - 1);
                if (in_dur >= 0 && in_dur < nc) wcsncpy(c->dur, cols[in_dur], STR - 1);
                if (in_fr >= 0 && in_fr < nc) wcsncpy(c->frames, cols[in_fr], STR - 1);
                if (in_img >= 0 && in_img < nc) wcsncpy(c->image, cols[in_img], STR - 1);
                if (in_ov >= 0 && in_ov < nc) wcsncpy(c->overlap, cols[in_ov], STR - 1);
                if (in_img2 >= 0 && in_img2 < nc)
                    wcsncpy(c->evName[1], cols[in_img2], STR - 1);
                if (in_fr2 >= 0 && in_fr2 < nc)
                    wcsncpy(c->evSec[0], cols[in_fr2], STR - 1);
                if (in_img3 >= 0 && in_img3 < nc)
                    wcsncpy(c->evName[2], cols[in_img3], STR - 1);
                if (in_fr3 >= 0 && in_fr3 < nc)
                    wcsncpy(c->evSec[1], cols[in_fr3], STR - 1);
                if (in_imgdir >= 0 && in_imgdir < nc)
                    wcsncpy(c->evDir, cols[in_imgdir], STR - 1);
                /*
                 * The first frame's file name and the folder come from the CSV.
                 * Only when the whole COL_IMG1 column is MISSING (a CSV written
                 * before v0.3.0) fall back to the long-standing default; an
                 * empty cell means the first step did not write that frame, and
                 * inventing a name for it would just copy nothing while looking
                 * like the material was complete.
                 */
                if (in_img1 >= 0 && in_img1 < nc)
                    wcsncpy(c->evName[0], cols[in_img1], STR - 1);
                else
                    wcscpy(c->evName[0], L"\u539F\u56FE.jpg");
                wcsncpy(c->user, c->detected, STR - 1);
                c->st = ST_PENDING;
                ++g_n;
            }
        }
        if (!nl) break;
        line = nl + 1;
    }
    free(buf);
    return g_n;
}

static FILE *open_utf8_out(const wchar_t *path, int with_bom)
{
    FILE *fp = _wfopen(path, L"wb");
    if (!fp) return NULL;
    if (with_bom) fwrite("\xEF\xBB\xBF", 1, 3, fp);
    return fp;
}

static void write_utf8_line(FILE *fp, wchar_t cols[][STR], int n)
{
    for (int i = 0; i < n; ++i) {
        char mb[4096];
        int r = WideCharToMultiByte(CP_UTF8, 0, cols[i], -1, mb, sizeof(mb) - 1, NULL, NULL);
        if (r <= 0) { mb[0] = 0; r = 1; }
        int need_q = (wcschr(cols[i], L',') != NULL || wcschr(cols[i], L'"') != NULL);
        if (need_q) {
            fputc('"', fp);
            for (int k = 0; k < r - 1; ++k) {
                if (mb[k] == '"') fputc('"', fp);
                fputc(mb[k], fp);
            }
            fputc('"', fp);
        } else {
            fwrite(mb, 1, (size_t)(r - 1), fp);
        }
        fputc(i == n - 1 ? '\n' : ',', fp);
    }
}

/* ---------------- Minimalist Flat geometry ----------------
 *
 * Spacing follows the style's generous-whitespace rule: a wide outer margin,
 * a clear vertical rhythm, and a lot of air around the image. There are no
 * grid lines and no colour blocks behind text; structure comes from whitespace
 * and from 2px black outlines only.
 */
/* review screen */
static Rct g_rHead, g_rImg, g_rDetEdit, g_rEdit;
static Rct g_rYes, g_rNo, g_rSkip, g_rPrev, g_rNext, g_rReport;
/* entry screen */
static Rct g_eField, g_eGo, g_eBrowse;
/* "violation details" prompt (location + heading), shown before the report */
static Rct g_wBox, g_wOk, g_wCancel;
/* index jump dialog */
static Rct g_iBox, g_iOk, g_iCancel;

/* hover / press tracking so buttons can invert their colours */
static int g_hover = 0;      /* control id under the cursor, 0 = none */
static int g_pressed = 0;

enum {
    ID_NONE = 0, ID_YES = 201, ID_NO = 202, ID_SKIP = 203,
    ID_PREV = 204, ID_NEXT = 205, ID_REPORT = 206,
    ID_GO = 102, ID_BROWSE = 101, ID_FIELD = 301, ID_EFIELD = 302
};

static void layout_review(void)
{
    /* the only remaining use of g_line is the focus/blur bookkeeping in the
     * edit subclass; keep it at 2 so nothing else depends on the old grid */
    g_line = MF_STROKE;

    int m = SC(48);                              /* outer margin: generous */
    int headH = SC(56);

    g_rHead.x = m; g_rHead.y = m;
    g_rHead.w = g_W - 2 * m; g_rHead.h = headH;

    int top = m + headH + SC(28);                /* air below the rule */
    int footer = g_H - m - SC(72);               /* buttons sit above bottom */


    /* ---- left column: fields stacked in a single column, with air ---- */
    int lw = SC(300);
    g_rDetEdit.x = m;
    g_rDetEdit.w = lw;
    g_rDetEdit.h = SC(40);
    g_rDetEdit.y = top + SC(28);                 /* below its label */

    g_rEdit.x = m;
    g_rEdit.w = lw;
    g_rEdit.h = SC(40);
    /* below: confidence + status/format row + its own label, each with air */
    g_rEdit.y = g_rDetEdit.y + g_rDetEdit.h + SC(200);

    /* ---- image card: takes the remaining width ---- */
    int ix = m + lw + SC(56);
    g_rImg.x = ix;
    g_rImg.y = top;
    g_rImg.w = g_W - m - ix;
    g_rImg.h = footer - top - SC(36);
    if (g_rImg.w < SC(240)) g_rImg.w = SC(240);
    if (g_rImg.h < SC(200)) g_rImg.h = SC(200);
    g_rImgCard = g_rImg;

    /* ---- action row: six buttons, equal widths ---- */
    int gap = SC(16);
    int total = g_W - 2 * m;
    int bw = (total - 5 * gap) / 6;
    if (bw < SC(90)) bw = SC(90);
    int by = g_H - m - SC(56);
    int bh = SC(56);

    g_rYes.x = m; g_rYes.y = by; g_rYes.w = bw; g_rYes.h = bh;
    g_rNo.x = m + (bw + gap); g_rNo.y = by; g_rNo.w = bw; g_rNo.h = bh;
    g_rSkip.x = m + 2 * (bw + gap); g_rSkip.y = by; g_rSkip.w = bw; g_rSkip.h = bh;
    g_rPrev.x = m + 3 * (bw + gap); g_rPrev.y = by; g_rPrev.w = bw; g_rPrev.h = bh;
    g_rNext.x = m + 4 * (bw + gap); g_rNext.y = by; g_rNext.w = bw; g_rNext.h = bh;
    g_rReport.x = m + 5 * (bw + gap); g_rReport.y = by; g_rReport.w = bw; g_rReport.h = bh;
}

static void layout_entry(void)
{
    g_line = MF_STROKE;
    int m = SC(48);
    int w = SC(420);
    int y = (int)(g_H * 0.42);
    g_eField.x = m; g_eField.y = y + SC(30); g_eField.w = w; g_eField.h = SC(44);
    g_eGo.x = m; g_eGo.y = g_eField.y + g_eField.h + SC(56);
    g_eGo.w = SC(180); g_eGo.h = SC(56);
    g_eBrowse.x = m + g_eGo.w + SC(16); g_eBrowse.y = g_eGo.y;
    g_eBrowse.w = SC(180); g_eBrowse.h = SC(56);
}

/*
 * Prompt for the violation details (place + travel direction) that the report
 * must carry. Shown after "finish report" validates every case, and before the
 * report is written.
 */
static void layout_where(void)
{
    int m = SC(48);
    int bw = g_W - 2 * m;
    int bh = SC(104);                        /* room for about 200 characters */
    g_wBox.x = m;
    g_wBox.y = (int)(g_H * 0.40);
    g_wBox.w = bw; g_wBox.h = bh;

    g_wOk.x = m; g_wOk.y = g_wBox.y + bh + SC(40);
    g_wOk.w = SC(180); g_wOk.h = SC(56);
    g_wCancel.x = m + g_wOk.w + SC(16); g_wCancel.y = g_wOk.y;
    g_wCancel.w = SC(180); g_wCancel.h = SC(56);
}


static int in_rect(const Rct *r, int x, int y)
{
    return x >= r->x && x < r->x + r->w && y >= r->y && y < r->y + r->h;
}

/* hit-test the review screen's controls, returning the control id */
static int hit_review(int x, int y)
{
    if (in_rect(&g_rYes, x, y)) return ID_YES;
    if (in_rect(&g_rNo, x, y)) return ID_NO;
    if (in_rect(&g_rSkip, x, y)) return ID_SKIP;
    if (in_rect(&g_rPrev, x, y)) return ID_PREV;
    if (in_rect(&g_rNext, x, y)) return ID_NEXT;
    if (in_rect(&g_rReport, x, y)) return ID_REPORT;
    return ID_NONE;
}

static int hit_entry(int x, int y)
{
    if (in_rect(&g_eGo, x, y)) return ID_GO;
    if (in_rect(&g_eBrowse, x, y)) return ID_BROWSE;
    if (in_rect(&g_eField, x, y)) return ID_EFIELD;
    return ID_NONE;
}

static int hit_where(int x, int y)
{
    if (in_rect(&g_wOk, x, y)) return ID_GO;
    if (in_rect(&g_wCancel, x, y)) return ID_BROWSE;
    if (in_rect(&g_wBox, x, y)) return ID_EFIELD;
    return ID_NONE;
}


/*
 * Filled button: black face, white text; hover inverts to white face, black
 * text. Pressed shows a short flat neutral flash. No movement, no scaling.
 */
static void button_filled(HDC dc, const Rct *r, const wchar_t *label, int id,
                          HFONT f)
{
    COLORREF face = MF_BLACK, text = MF_WHITE;
    if (g_pressed == id) { face = MF_PRESSED; text = MF_BLACK; }
    else if (g_hover == id) { face = MF_WHITE; text = MF_BLACK; }
    fill_rect(dc, r->x, r->y, r->w, r->h, face);
    frame_2px(dc, r->x, r->y, r->w, r->h, MF_BLACK);
    draw_text(dc, label, r->x, r->y, r->w, r->h, f, text,
              DT_CENTER | DT_VCENTER | DT_SINGLELINE);
}

/* Outlined button: white face, black text; hover inverts to black face. */
static void button_outline(HDC dc, const Rct *r, const wchar_t *label, int id,
                           HFONT f)
{
    COLORREF face = MF_WHITE, text = MF_BLACK;
    if (g_pressed == id) { face = MF_PRESSED; text = MF_BLACK; }
    else if (g_hover == id) { face = MF_BLACK; text = MF_WHITE; }
    fill_rect(dc, r->x, r->y, r->w, r->h, face);
    frame_2px(dc, r->x, r->y, r->w, r->h, MF_BLACK);
    draw_text(dc, label, r->x, r->y, r->w, r->h, f, text,
              DT_CENTER | DT_VCENTER | DT_SINGLELINE);
}

/* small uppercase tracked label, as the style prescribes for field labels */
static void label_small(HDC dc, const wchar_t *s, int x, int y, int w,
                        COLORREF col)
{
    draw_text(dc, s, x, y, w, SC(18), g_fLabel, col,
              DT_LEFT | DT_VCENTER | DT_SINGLELINE);
}

static void paint_review(HDC dc)
{
    /* pure white page */
    fill_rect(dc, 0, 0, g_W, g_H, MF_WHITE);

    /* ---- header: title left, counters right, then a 2px rule ---- */
    draw_text(dc, L"PLATE REVIEW", g_rHead.x, g_rHead.y, g_rHead.w, g_rHead.h,
              g_fTitle, MF_BLACK, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
    {
        int done = 0, sk = 0;
        for (int i = 0; i < g_n; ++i) {
            if (g_cases[i].st == ST_YES || g_cases[i].st == ST_NO) ++done;
            if (g_cases[i].st == ST_SKIP) ++sk;
        }
        wchar_t buf[192];
        _snwprintf(buf, 191,
            L"#%d / %d      \u5DF2\u5B9A %d      \u8DF7\u8FC7 %d      \u7F29\u653E %d%%",
            g_idx + 1, g_n, done, sk, g_zoom);
        draw_text(dc, buf, g_rHead.x, g_rHead.y, g_rHead.w, g_rHead.h,
                  g_fBody, MF_MUTED, DT_RIGHT | DT_VCENTER | DT_SINGLELINE);
    }
    underline(dc, g_rHead.x, g_rHead.y + g_rHead.h, g_rHead.w, MF_BLACK);

    Case *c = &g_cases[g_idx];

    /* ---- left column ---- */
    label_small(dc, L"\u68C0\u6D4B\u8F66\u724C", g_rDetEdit.x,
                g_rDetEdit.y - SC(24), g_rDetEdit.w, MF_MUTED);
    underline(dc, g_rDetEdit.x, g_rDetEdit.y + g_rDetEdit.h,
              g_rDetEdit.w,
              (GetFocus() == g_editDet) ? MF_ACCENT : MF_BLACK);

    /* confidence: plain black text, read-only, in the left column */
    {
        int cx = g_rDetEdit.x;
        int cy = g_rDetEdit.y + g_rDetEdit.h + SC(22);
        label_small(dc, L"\u7F6E\u4FE1\u5EA6", cx, cy - SC(18),
                    g_rDetEdit.w, MF_MUTED);
        wchar_t cf[64];
        if (c->conf[0]) _snwprintf(cf, 63, L"%.4f", _wtof(c->conf));
        else wcscpy(cf, L"0.0000");
        draw_text(dc, cf, cx, cy, g_rDetEdit.w, SC(28),
                  g_fBody, MF_BLACK, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
    }

    /* status chip and format indicator, side by side in the left column.
     * Both always carry their own word, so state is never colour-only. */
    {
        int cy = g_rDetEdit.y + g_rDetEdit.h + SC(86);
        const wchar_t *st = L"\u5F85\u5B9A";
        COLORREF face = MF_WHITE, text = MF_BLACK;
        if (c->st == ST_YES) { st = L"\u662F"; face = MF_BLACK; text = MF_WHITE; }
        else if (c->st == ST_NO) { st = L"\u5426"; face = MF_ACCENT; text = MF_WHITE; }
        else if (c->st == ST_SKIP) { st = L"\u8DF3\u8FC7"; face = MF_NEUTRAL; }

        label_small(dc, L"\u72B6\u6001", g_rDetEdit.x, cy - SC(18),
                    SC(120), MF_MUTED);
        Rct chip = { g_rDetEdit.x, cy, SC(112), SC(34) };
        fill_rect(dc, chip.x, chip.y, chip.w, chip.h, face);
        frame_2px(dc, chip.x, chip.y, chip.w, chip.h, MF_BLACK);
        draw_text(dc, st, chip.x, chip.y, chip.w, chip.h, g_fBody, text,
                  DT_CENTER | DT_VCENTER | DT_SINGLELINE);

        int fx = g_rDetEdit.x + SC(140);
        label_small(dc, L"\u683C\u5F0F", fx, cy - SC(18), SC(160), MF_MUTED);
        const wchar_t *probe = c->user;
        if (g_editDet && GetFocus() == g_editDet) probe = c->detected;
        BOOL okfmt = plate_ok(probe);
        Rct fb = { fx, cy, SC(112), SC(34) };
        fill_rect(dc, fb.x, fb.y, fb.w, fb.h, okfmt ? MF_BLACK : MF_ACCENT);
        frame_2px(dc, fb.x, fb.y, fb.w, fb.h, MF_BLACK);
        draw_text(dc, okfmt ? L"OK" : L"\u683C\u5F0F", fb.x, fb.y, fb.w, fb.h,
                  g_fBody, MF_WHITE, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    }

    /* confirm field */
    label_small(dc, L"\u786E\u8BA4\u8F66\u724C", g_rEdit.x,
                g_rEdit.y - SC(24), g_rEdit.w, MF_MUTED);
    underline(dc, g_rEdit.x, g_rEdit.y + g_rEdit.h, g_rEdit.w,
              (GetFocus() == g_edit) ? MF_ACCENT : MF_BLACK);

    /* ---- image card: 2px black outline, white inside, image inset ---- */
    fill_rect(dc, g_rImg.x, g_rImg.y, g_rImg.w, g_rImg.h, MF_WHITE);
    frame_2px(dc, g_rImg.x, g_rImg.y, g_rImg.w, g_rImg.h, MF_BLACK);
    {
        int ix = g_rImg.x + SC(16), iy = g_rImg.y + SC(16);
        int iw = g_rImg.w - SC(32), ih = g_rImg.h - SC(32);
        if (g_img && iw > 0 && ih > 0) {
            double z = g_zoom / 100.0;
            int dw = (int)(g_basew * z), dh = (int)(g_baseh * z);
            if (dw < 1) dw = 1;
            if (dh < 1) dh = 1;

            double sx0 = 0, sy0 = 0, sw = (double)g_iw, sh = (double)g_ih;
            if (g_zoom > 100) {
                sw = (double)g_iw * g_basew / dw;
                sh = (double)g_ih * g_baseh / dh;
                if (sw > g_iw) sw = (double)g_iw;
                if (sh > g_ih) sh = (double)g_ih;
                sx0 = ((double)g_iw - sw) / 2.0
                      - (double)g_panx * g_iw / (double)dw;
                sy0 = ((double)g_ih - sh) / 2.0
                      - (double)g_pany * g_ih / (double)dh;
                if (sx0 < 0) sx0 = 0;
                if (sy0 < 0) sy0 = 0;
                if (sx0 + sw > g_iw) sx0 = g_iw - sw;
                if (sy0 + sh > g_ih) sy0 = g_ih - sh;
            }
            int px = ix + (iw - dw) / 2 + (g_zoom > 100 ? 0 : g_panx);
            int py = iy + (ih - dh) / 2 + (g_zoom > 100 ? 0 : g_pany);

            Gdiplus::Graphics g(dc);
            Gdiplus::Region clip(Gdiplus::Rect(ix, iy, iw, ih));
            g.SetClip(&clip);
            g.SetInterpolationMode(g_zoom > 100
                ? Gdiplus::InterpolationModeNearestNeighbor
                : Gdiplus::InterpolationModeHighQualityBicubic);
            g.SetPixelOffsetMode(Gdiplus::PixelOffsetModeHalf);
            g.DrawImage(g_img, Gdiplus::Rect(px, py, dw, dh),
                        (INT)sx0, (INT)sy0, (INT)sw, (INT)sh,
                        Gdiplus::UnitPixel);
            g.ResetClip();
        } else {
            draw_text(dc, L"\u65E0\u8BC1\u636E\u56FE", ix, iy, iw, ih,
                      g_fBody, MF_MUTED, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        }
    }
    /* quiet helper line under the image, de-emphasised on purpose */
    draw_text(dc, L"\u6EDA\u8F6E\u7F29\u653E / \u62D6\u52A8\u5E73\u79FB / \u53CC\u51FB\u590D\u4F4D",
              g_rImg.x, g_rImg.y + g_rImg.h + SC(8), g_rImg.w, SC(20),
              g_fSmall, MF_MUTED, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
    draw_text(dc, L"\u8BC1\u636E\u56FE", g_rImg.x, g_rImg.y + g_rImg.h + SC(8),
              g_rImg.w, SC(20), g_fSmall, MF_MUTED,
              DT_RIGHT | DT_VCENTER | DT_SINGLELINE);

    /* ---- action row ---- */
    button_filled(dc, &g_rYes, L"\u662F", ID_YES, g_fBody);
    button_outline(dc, &g_rNo, L"\u5426", ID_NO, g_fBody);
    button_outline(dc, &g_rSkip, L"\u8DF3\u8FC7", ID_SKIP, g_fBody);
    button_outline(dc, &g_rPrev, L"\u4E0A\u4E00\u5F20", ID_PREV, g_fBody);
    button_outline(dc, &g_rNext, L"\u8F6C\u81F3\u672A\u786E\u8BA4", ID_NEXT, g_fBody);
    button_outline(dc, &g_rReport, L"\u5B8C\u6210\u62A5\u544A", ID_REPORT, g_fBody);
}

/* ---------------- entry screen ---------------- */

static void paint_entry(HDC dc)
{
    fill_rect(dc, 0, 0, g_W, g_H, MF_WHITE);

    int m = SC(48);

    /* hero: large tight headline plus one de-emphasised line */
    draw_text(dc, L"PLATE REVIEW", m, (int)(g_H * 0.16), g_W - 2 * m, SC(80),
              g_fHero, MF_BLACK, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
    draw_text(dc, L"\u5E94\u6025\u8F66\u9053\u8F66\u724C\u4EBA\u5DE5\u5BA1\u6838",
              m, (int)(g_H * 0.16) + SC(84), g_W - 2 * m, SC(28),
              g_fBody, MF_MUTED, DT_LEFT | DT_VCENTER | DT_SINGLELINE);

    label_small(dc, L"\u521D\u6210\u679C\u76EE\u5F55", g_eField.x,
                g_eField.y - SC(26), g_eField.w, MF_MUTED);

    /* the folder field is a real EDIT (placed over this area) so an external
     * path can be pasted straight in; only the underline is drawn here */
    underline(dc, g_eField.x, g_eField.y + g_eField.h, g_eField.w,
              (GetFocus() == g_editEntry) ? MF_ACCENT : MF_BLACK);

    if (g_entryErr[0]) {
        draw_text(dc, g_entryErr, g_eField.x,
                  g_eField.y + g_eField.h + SC(10), g_eField.w, SC(22),
                  g_fSmall, MF_ACCENT, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
    }

    button_filled(dc, &g_eGo, L"\u8FDB\u5165", ID_GO, g_fBody);
    button_outline(dc, &g_eBrowse, L"\u6D4F\u89C8", ID_BROWSE, g_fBody);

    /* deliberately muted footnote */
    draw_text(dc, L"\u8DEF\u5F84\u4E0D\u5F97\u5305\u542B\u4E2D\u6587",
              m, g_H - m - SC(20), g_W - 2 * m, SC(20), g_fSmall, MF_MUTED,
              DT_LEFT | DT_VCENTER | DT_SINGLELINE);
}

/* ---------------- violation details prompt ----------------
 *
 * Shown before the report is written: the operator supplies the place of the
 * violation and the vehicle's direction of travel, as free text up to 200
 * characters. The text is echoed into the report.
 */
static void paint_where(HDC dc)
{
    fill_rect(dc, 0, 0, g_W, g_H, MF_WHITE);

    int m = SC(48);
    draw_text(dc, L"PLATE REVIEW", m, (int)(g_H * 0.14), g_W - 2 * m, SC(64),
              g_fTitle, MF_BLACK, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
    draw_text(dc, L"\u8FDD\u6CD5\u5730\u70B9\u4E0E\u884C\u9A76\u65B9\u5411",
              m, (int)(g_H * 0.14) + SC(70), g_W - 2 * m, SC(28),
              g_fBody, MF_MUTED, DT_LEFT | DT_VCENTER | DT_SINGLELINE);

    label_small(dc, L"\u8FDD\u6CD5\u5730\u70B9 / \u884C\u9A76\u65B9\u5411",
                g_wBox.x, g_wBox.y - SC(26), g_wBox.w, MF_MUTED);

    /* the text itself lives in a real multiline EDIT placed over this box,
     * so it can be pasted in and wraps; only the frame is drawn here */
    frame_2px(dc, g_wBox.x - SC(6), g_wBox.y - SC(6),
              g_wBox.w + SC(12), g_wBox.h + SC(12),
              (GetFocus() == g_editWhere) ? MF_ACCENT : MF_BLACK);

    {
        wchar_t cnt[64];
        size_t n = wcslen(g_where);
        _snwprintf(cnt, 63, L"%d / 200", (int)n);
        draw_text(dc, cnt, g_wBox.x, g_wBox.y + g_wBox.h + SC(8),
                  g_wBox.w, SC(20), g_fSmall,
                  (n > 200) ? MF_ACCENT : MF_MUTED,
                  DT_RIGHT | DT_VCENTER | DT_SINGLELINE);
    }

    if (g_whereErr) {
        draw_text(dc, L"\u8BF7\u586B\u5199\u8FDD\u6CD5\u5730\u70B9\u4E0E\u884C\u9A76\u65B9\u5411"
                      L"\uFF08\u6700\u591A 200 \u5B57\uFF09",
                  g_wBox.x, g_wBox.y + g_wBox.h + SC(8), g_wBox.w - SC(120), SC(20),
                  g_fSmall, MF_ACCENT, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
    }

    button_filled(dc, &g_wOk, L"\u751F\u6210\u62A5\u544A", ID_GO, g_fBody);
    button_outline(dc, &g_wCancel, L"\u8FD4\u56DE", ID_BROWSE, g_fBody);
}

/* ---------------- business actions ---------------- */

static void do_enter(void);
static void mark(int st);
static void goto_skip(void);
/* ---------------- report generation ----------------
 *
 * Writes the violation report (HTML) next to the CSV, in the review-output
 * folder whose name is written as \u escapes in the code below.
 *
 * Two rules matter here:
 *   1. the plate printed in the report is the USER-CONFIRMED one (c->user),
 *      never the raw detection;
 *   2. the evidence picture is the UNANNOTATED video frame taken from the
 *      "evidence images / originals" sub-folder, not the ROI-annotated image
 *      shown while reviewing.
 *      The annotated one exists to help a human judge; the report is a
 *      document that goes out, so its evidence must be the untouched frame.
 *
 * Images are inlined as base64 data URIs, so the report is one portable file
 * with no external dependencies.
 */

static const char B64TAB[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static void b64_encode(const unsigned char *src, size_t n, char **out)
{
    size_t olen = 4 * ((n + 2) / 3);
    char *o = (char *)malloc(olen + 1);
    if (!o) { *out = NULL; return; }
    size_t i = 0, k = 0;
    while (i + 2 < n) {
        unsigned v = ((unsigned)src[i] << 16) | ((unsigned)src[i + 1] << 8) |
                     (unsigned)src[i + 2];
        o[k++] = B64TAB[(v >> 18) & 63];
        o[k++] = B64TAB[(v >> 12) & 63];
        o[k++] = B64TAB[(v >> 6) & 63];
        o[k++] = B64TAB[v & 63];
        i += 3;
    }
    if (i < n) {
        unsigned v = (unsigned)src[i] << 16;
        int rem = (int)(n - i);
        if (rem == 2) v |= (unsigned)src[i + 1] << 8;
        o[k++] = B64TAB[(v >> 18) & 63];
        o[k++] = B64TAB[(v >> 12) & 63];
        o[k++] = (rem == 2) ? B64TAB[(v >> 6) & 63] : '=';
        o[k++] = '=';
    }
    o[k] = 0;
    *out = o;
}

/* read a whole file; caller frees. NULL on failure */
static unsigned char *read_file(const wchar_t *path, size_t *len)
{
    FILE *fp = _wfopen(path, L"rb");
    if (!fp) return NULL;
    fseek(fp, 0, SEEK_END);
    long sz = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    if (sz <= 0) { fclose(fp); return NULL; }
    unsigned char *buf = (unsigned char *)malloc((size_t)sz);
    if (!buf) { fclose(fp); return NULL; }
    size_t rd = fread(buf, 1, (size_t)sz, fp);
    fclose(fp);
    *len = rd;
    return buf;
}

static void fputws_utf8(FILE *fp, const wchar_t *s)
{
    if (!s) return;
    char mb[8192];
    int r = WideCharToMultiByte(CP_UTF8, 0, s, -1, mb, sizeof(mb) - 1, NULL, NULL);
    if (r > 0) fwrite(mb, 1, (size_t)(r - 1), fp);
}

static void fputs_html_escaped(FILE *fp, const wchar_t *s)
{
    if (!s) return;
    for (const wchar_t *p = s; *p; ++p) {
        char mb[8];
        int r;
        switch (*p) {
        case L'&': fputs("&amp;", fp); continue;
        case L'<': fputs("&lt;", fp); continue;
        case L'>': fputs("&gt;", fp); continue;
        case L'"': fputs("&quot;", fp); continue;
        default: break;
        }
        r = WideCharToMultiByte(CP_UTF8, 0, p, 1, mb, sizeof(mb) - 1, NULL, NULL);
        if (r > 0) fwrite(mb, 1, (size_t)r, fp);
    }
}

static int starts_with(const wchar_t *s, const wchar_t *pfx)
{
    if (!s || !pfx) return 0;
    return wcsncmp(s, pfx, wcslen(pfx)) == 0;
}

/* byte copy; returns 1 on success. No CRT "safe" variant is available here
 * (this is plain Win32 C++), and the files are a few hundred KB at most. */
static int copy_file(const wchar_t *src, const wchar_t *dst)
{
    if (!src || !dst || !src[0] || !dst[0]) return 0;
    FILE *in = _wfopen(src, L"rb");
    if (!in) return 0;
    FILE *out = _wfopen(dst, L"wb");
    if (!out) { fclose(in); return 0; }
    static char buf[65536];
    size_t rd;
    int ok = 1;
    while ((rd = fread(buf, 1, sizeof(buf), in)) > 0) {
        if (fwrite(buf, 1, rd, out) != rd) { ok = 0; break; }
    }
    fclose(in);
    fclose(out);
    return ok;
}

/*
 * ---------------- evidence packaging (v0.3.0) ----------------
 *
 * After the operator presses "generate report", every evidence frame of one
 * confirmed case is filed into that case's plate-named folder:
 *
 *     <plate>/
 *       01_<primary>.jpg    highest-confidence frame (report body uses this)
 *       01_<frame2>.jpg     earliest-nearby frame
 *       01_<frame3>.jpg     latest-nearby frame
 *
 * (the Chinese file-name parts are built with \u escapes below, since this file
 *  must stay 100% ASCII - see the note at the top of it)
 *
 * Design notes:
 *   - the frames are COPIED, not moved: the review screen and a re-generated
 *     report both read the "evidence images / originals" sub-folder of the
 *     initial folder, so moving would break them;
 *   - WHICH frames to use is decided in Python (pipeline_v2 / merge_tracks), not
 *     here: that needs the whole track's candidate pool. This side only copies
 *     the frames the CSV names.
 *   - a missing frame only costs that one file; the counts are reported back.
 */

/*
 * File every evidence frame of one confirmed case into its plate-named folder.
 * Must be called after the CSV loop, which creates the folder.
 */
/*
 * File the evidence frames of one confirmed case into its plate-named folder.
 *
 * This side does NO choosing and NO guessing: the first step wrote the three
 * frames into one per-case folder and the CSV names both the folder and the
 * files. All that is left is "the operator pressed yes for this plate, so copy
 * its frames over under descriptive names".
 *
 * Returns the number of frames filed.
 */
static int package_case(Case *c, const wchar_t *outdir, int idx)
{
    wchar_t pdir[MAX_PATH * 2];
    _snwprintf(pdir, MAX_PATH * 2 - 1, L"%s\\%s", outdir, c->user);
    if (GetFileAttributesW(pdir) == INVALID_FILE_ATTRIBUTES &&
        !CreateDirectoryW(pdir, NULL))
        return 0;

    static const wchar_t *out_suffix[3] = {
        L"\u539F\u56FE.jpg",        /* primary */
        L"\u8BC1\u636E\u5E272.jpg", /* frame 2 */
        L"\u8BC1\u636E\u5E273.jpg"  /* frame 3 */
    };

    int filed = 0;
    for (int k = 0; k < 3; ++k) {
        if (!c->evName[k][0]) continue;
        wchar_t src[MAX_PATH * 2], dst[MAX_PATH * 2];
        if (c->evDir[0]) {
            _snwprintf(src, MAX_PATH * 2 - 1,
                       L"%s\\\u8BC1\u636E\u56FE\\%s\\%s",
                       g_folder, c->evDir, c->evName[k]);
        } else {
            /* older CSV: no per-case folder, frames sat next to the review images */
            src[0] = 0;
        }
        if (!src[0] || !file_exists(src)) continue;
        _snwprintf(dst, MAX_PATH * 2 - 1, L"%s\\%02d_%s", pdir, idx, out_suffix[k]);
        if (copy_file(src, dst)) {
            ++c->evCopied[k];
            ++filed;
        }
    }
    return filed;
}

/*
 * Locate one evidence frame written by the first step, inside a per-case folder.
 * 1 = plain evidence frame found, 0 = only the ROI-annotated review image was
 * found (the report then says so), -1 = nothing at all.
 */
static int find_evidence_image(const wchar_t *dir, const wchar_t *img_name,
                               wchar_t *out, int cch)
{
    wchar_t p[MAX_PATH * 2];
    if (dir && dir[0]) {
        _snwprintf(p, MAX_PATH * 2 - 1, L"%s\\\u8BC1\u636E\u56FE\\%s\\%s",
                   g_folder, dir, img_name);
        if (file_exists(p)) { wcsncpy(out, p, (size_t)cch - 1); out[cch - 1] = 0; return 1; }
    }
    /* fall back to the annotated review image of the same number */
    _snwprintf(p, MAX_PATH * 2 - 1, L"%s\\\u8BC1\u636E\u56FE\\%s",
               g_folder, img_name);
    if (file_exists(p)) { wcsncpy(out, p, (size_t)cch - 1); out[cch - 1] = 0; return 0; }
    out[0] = 0;
    return -1;
}

static void write_html_report(const wchar_t *path, int linked)
{
    FILE *fp = _wfopen(path, L"wb");
    if (!fp) return;
    fwrite("\xEF\xBB\xBF", 1, 3, fp);          /* BOM so browsers pick UTF-8 */

    int nYes = 0, nNo = 0, nSkip = 0;
    for (int i = 0; i < g_n; ++i) {
        if (g_cases[i].st == ST_YES) ++nYes;
        else if (g_cases[i].st == ST_NO) ++nNo;
        else if (g_cases[i].st == ST_SKIP) ++nSkip;
    }

    /*
     * Minimalist Flat: pure black/white plus one accent, zero shadows, zero
     * gradients, square corners, generous whitespace, hover inverts colours.
     * Only ASCII goes through fputs(); every Chinese string goes through
     * fputws_utf8() with a wide literal.
     */
    fputs("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">", fp);
    fputws_utf8(fp, L"<title>\u5E94\u6025\u8F66\u9053\u8FDD\u7AE0\u68C0\u6D4B"
                    L"\u62A5\u544A</title>");
    fputs("<style>\n"
          "*{box-sizing:border-box;}\n"
          "body{margin:0;padding:48px 32px;background:#fff;color:#000;"
          "font-family:'Microsoft YaHei UI','Segoe UI',sans-serif;}\n"
          ".wrap{max-width:1100px;margin:0 auto;}\n"
          "h1{font-size:44px;font-weight:800;letter-spacing:-.02em;margin:0 0 8px;}\n"
          ".sub{color:#6b6b6b;margin:0 0 40px;font-size:15px;}\n"
          ".rule{border-top:2px solid #000;margin:0 0 32px;}\n"
          ".grid{display:grid;grid-template-columns:repeat(4,1fr);"
          "border:2px solid #000;border-right:0;margin-bottom:48px;}\n"
          ".grid div{padding:20px;border-right:2px solid #000;}\n"
          ".k{font-size:11px;font-weight:700;letter-spacing:.14em;"
          "text-transform:uppercase;color:#6b6b6b;margin-bottom:6px;}\n"
          ".v{font-size:26px;font-weight:700;word-break:break-all;}\n"
          ".accent{color:#ff3366;}\n"
          "h2{font-size:24px;font-weight:700;margin:0 0 24px;}\n"
          ".card{border:2px solid #000;padding:28px;margin-bottom:24px;}\n"
          ".plate{font-size:32px;font-weight:800;letter-spacing:.02em;"
          "margin-bottom:16px;}\n"
          "table{border-collapse:collapse;width:100%;}\n"
          "td,th{padding:10px 14px;text-align:left;"
          "border-bottom:2px solid #000;font-size:15px;}\n"
          "tr:last-child td{border-bottom:0;}\n"
          "img{display:block;max-width:100%;border:2px solid #000;margin-top:20px;}\n"
          ".note{color:#6b6b6b;font-size:13px;margin-top:8px;}\n"
          ".where{border:2px solid #000;padding:28px;margin-bottom:24px;}\n"
          ".wheretext{font-size:20px;font-weight:600;line-height:1.6;"
          "white-space:pre-wrap;word-break:break-word;}\n"
          "</style></head><body><div class=\"wrap\">\n", fp);

    fputs("<h1>PLATE REVIEW</h1>\n", fp);
    fputws_utf8(fp, L"<p class=\"sub\">\u5E94\u6025\u8F66\u9053\u8FDD\u7AE0"
                    L"\u68C0\u6D4B\u62A5\u544A / \u4EBA\u5DE5\u786E\u8BA4"
                    L"\u7ED3\u679C</p>\n");
    fputs("<div class=\"rule\"></div>\n", fp);

    /*
     * Violation place and travel direction, supplied by the operator before the
     * report is generated. Shown as its own block so it reads as a statement of
     * fact about the case rather than as a statistic.
     */
    if (g_where[0]) {
        fputs("<div class=\"where\">\n<div class=\"k\">", fp);
        fputws_utf8(fp, L"\u8FDD\u6CD5\u5730\u70B9\u4E0E\u884C\u9A76\u65B9\u5411");
        fputs("</div>\n<div class=\"wheretext\">", fp);
        fputs_html_escaped(fp, g_where);
        fputs("</div>\n</div>\n", fp);
    }

    /* ---- summary ---- */
    {
        wchar_t buf[512];
        fputs("<div class=\"grid\">\n", fp);
        fputs("<div><div class=\"k\">", fp);
        fputws_utf8(fp, L"\u521D\u6210\u679C\u76EE\u5F55");
        fputs("</div><div class=\"v\" style=\"font-size:14px;\">", fp);
        fputs_html_escaped(fp, g_folder);
        fputs("</div></div>\n", fp);

        fputs("<div><div class=\"k\">", fp);
        fputws_utf8(fp, L"\u6848\u4F8B\u603B\u6570");
        _snwprintf(buf, 511, L"</div><div class=\"v\">%d</div></div>\n", g_n);
        fputws_utf8(fp, buf);

        fputs("<div><div class=\"k\">", fp);
        fputws_utf8(fp, L"\u786E\u8BA4\u4E0A\u62A5\uFF08\u662F\uFF09");
        _snwprintf(buf, 511, L"</div><div class=\"v accent\">%d</div></div>\n", nYes);
        fputws_utf8(fp, buf);

        fputs("<div><div class=\"k\">", fp);
        fputws_utf8(fp, L"\u5426\u5B9A / \u8DF3\u8FC7");
        _snwprintf(buf, 511, L"</div><div class=\"v\">%d / %d</div></div>\n",
                   nNo, nSkip);
        fputws_utf8(fp, buf);
        fputs("</div>\n", fp);
    }

    /* ---- detail: only the confirmed ("yes") cases are part of the report ---- */
    fputs("<h2>", fp);
    fputws_utf8(fp, L"\u8FDD\u7AE0\u8BE6\u60C5");
    fputs("</h2>\n", fp);

    int shown = 0;
    for (int i = 0; i < g_n; ++i) {
        Case *c = &g_cases[i];
        if (c->st != ST_YES) continue;
        ++shown;
        wchar_t buf[1024];

        fputs("<div class=\"card\">\n<div class=\"k\">", fp);
        fputws_utf8(fp, L"\u8FDD\u7AE0");
        _snwprintf(buf, 1023, L" #%d</div>\n", i + 1);
        fputws_utf8(fp, buf);

        fputs("<div class=\"plate\">", fp);
        fputs_html_escaped(fp, c->user);              /* confirmed plate */
        fputs("</div>\n<table>\n", fp);

        fputs("<tr><td class=\"k\">", fp);
        fputws_utf8(fp, L"\u68C0\u6D4B\u8F66\u724C");
        fputs("</td><td>", fp);
        fputs_html_escaped(fp, c->detected);
        fputs("</td></tr>\n", fp);

        fputs("<tr><td class=\"k\">", fp);
        fputws_utf8(fp, L"\u7F6E\u4FE1\u5EA6");
        _snwprintf(buf, 1023, L"</td><td>%.4f</td></tr>\n", _wtof(c->conf));
        fputws_utf8(fp, buf);

        fputs("<tr><td class=\"k\">", fp);
        fputws_utf8(fp, L"\u65F6\u95F4");
        fputs("</td><td>", fp);
        fputs_html_escaped(fp, c->ts);
        fputs(" ~ ", fp);
        fputs_html_escaped(fp, c->tsEnd);
        fputs("</td></tr>\n", fp);

        fputs("<tr><td class=\"k\">", fp);
        fputws_utf8(fp, L"\u51FA\u73B0\u65F6\u957F / \u5E27\u6570");
        _snwprintf(buf, 1023, L"</td><td>%s \u79D2 / %s \u5E27</td></tr>\n",
                   c->dur, c->frames);
        fputws_utf8(fp, buf);

        fputs("<tr><td class=\"k\">", fp);
        fputws_utf8(fp, L"\u5E94\u6025\u8F66\u9053\u91CD\u53E0");
        _snwprintf(buf, 1023, L"</td><td>%s</td></tr>\n", c->overlap);
        fputws_utf8(fp, buf);
        fputs("</table>\n", fp);

        /*
         * The three evidence frames the first step picked and wrote:
         *   1 primary  = highest-confidence frame (report body evidence)
         *   2 earliest = the earliest-nearby usable frame
         *   3 latest   = the latest-nearby usable frame
         * The CSV names all three; a case with too few usable frames simply has
         * fewer, and that is said in the notes rather than padded out with a
         * duplicate picture.
         */
        {
            static const wchar_t *alt_alt[3] = { L"\u8BC1\u636E\u56FE",
                                                 L"\u8BC1\u636E\u56FE2",
                                                 L"\u8BC1\u636E\u56FE3" };
            static const wchar_t *alt_lbl[3] = {
                L"\u539F\u56FE\uFF08\u7F6E\u4FE1\u5EA6\u6700\u9AD8\u5E27\uFF09",
                L"\u8BC1\u636E\u56FE 2\uFF08\u6700\u65E9\u9644\u8FD1\u5E27\uFF09",
                L"\u8BC1\u636E\u56FE 3\uFF08\u6700\u665A\u9644\u8FD1\u5E27\uFF09" };
            /* the names the frames were filed under, used by the linked variant */
            static const wchar_t *out_suffix_link[3] = {
                L"%02d_\u539F\u56FE.jpg",
                L"%02d_\u8BC1\u636E\u5E272.jpg",
                L"%02d_\u8BC1\u636E\u5E273.jpg" };
            const wchar_t *secs[3];
            secs[0] = NULL;
            secs[1] = c->evSec[0];
            secs[2] = c->evSec[1];

            int got = 0;
            for (int k = 0; k < 3; ++k) {
                if (!c->evName[k][0]) continue;
                wchar_t fpath[MAX_PATH * 2];
                int kind = find_evidence_image(c->evDir, c->evName[k], fpath,
                                              (int)ARRAYSZ(fpath));
                if (kind < 0) continue;

                fputs("<div class=\"k\" style=\"margin-top:24px;\">", fp);
                fputws_utf8(fp, alt_lbl[k]);
                fputs("</div>\n", fp);
                if (linked) {
                    /*
                     * Light-weight variant: link the file instead of inlining it,
                     * so this report is a few KB. Only useful next to the
                     * evidence folders (which is where it is written).
                     */
                    fputs("<img alt=\"", fp);
                    fputws_utf8(fp, alt_alt[k]);
                    fputs("\" src=\"", fp);
                    fputws_utf8(fp, c->user);
                    fputs("/", fp);
                    wchar_t link_name[STR];
                    _snwprintf(link_name, STR - 1, out_suffix_link[k], i + 1);
                    fputws_utf8(fp, link_name);
                    fputs("\">\n", fp);
                } else {
                    size_t l = 0;
                    unsigned char *raw = read_file(fpath, &l);
                    if (!raw) continue;
                    char *b64 = NULL;
                    b64_encode(raw, l, &b64);
                    free(raw);
                    if (!b64) continue;
                    fputs("<img alt=\"", fp);
                    fputws_utf8(fp, alt_alt[k]);
                    fputs("\" src=\"data:image/jpeg;base64,", fp);
                    fwrite(b64, 1, strlen(b64), fp);
                    fputs("\">\n", fp);
                    free(b64);
                }
                ++got;

                if (secs[k] && secs[k][0]) {
                    fputs("<p class=\"note\">", fp);
                    fputws_utf8(fp, L"\u8BE5\u5E27\u5728\u89C6\u9891\u5185\u7B2C ");
                    fputs_html_escaped(fp, secs[k]);
                    fputws_utf8(fp, L" \u79D2");
                    fputs("</p>\n", fp);
                } else if (kind == 0) {
                    fputs("<p class=\"note\">", fp);
                    fputws_utf8(fp, L"\u6CE8\uFF1A\u672A\u627E\u5230\u672A\u6807\u6CE8"
                                    L"\u539F\u56FE\uFF0C\u6B64\u5904\u4F7F\u7528\u542B "
                                    L"ROI \u6807\u6CE8\u7684\u56FE\u3002");
                    fputs("</p>\n", fp);
                }
            }
            if (got == 0) {
                fputs("<p class=\"note\">", fp);
                fputws_utf8(fp, L"\u672A\u627E\u5230\u8BC1\u636E\u56FE");
                fputs("</p>\n", fp);
            } else if (got < 3) {
                wchar_t mark[128];
                _snwprintf(mark, ARRAYSZ(mark) - 1,
                           L"\u53EA\u6709 %d \u5F20\u8BC1\u636E\u56FE"
                           L"\uFF08\u53EF\u8FA8\u8BC6\u5E27\u4E0D\u591F"
                           L"\uFF0C\u4E0D\u62FF\u540C\u4E00\u5E27\u51D1\u6570\uFF09",
                           got);
                fputs("<p class=\"note\">", fp);
                fputws_utf8(fp, mark);
                fputs("</p>\n", fp);
            }
        }
        fputs("</div>\n", fp);
    }
    if (shown == 0) {
        fputs("<p class=\"note\">", fp);
        fputws_utf8(fp, L"\u6CA1\u6709\u786E\u8BA4\u4E0A\u62A5\uFF08\u662F\uFF09"
                        L"\u7684\u6848\u4F8B\u3002");
        fputs("</p>\n", fp);
    }

    fputs("</div></body></html>\n", fp);
    fclose(fp);
}

static void finish_report(void);
static void browse_folder(void);

static void show_case_apply(void)
{
    if (g_n <= 0) return;
    if (g_idx < 0) g_idx = 0;
    if (g_idx >= g_n) g_idx = g_n - 1;
    Case *c = &g_cases[g_idx];

    if (g_editDet && IsWindow(g_editDet)) SetWindowTextW(g_editDet, c->detected);
    if (g_edit && IsWindow(g_edit)) SetWindowTextW(g_edit, c->user);
    place_edits();
    /* load the evidence image for this case */
    wchar_t ip[MAX_PATH * 2];
    ip[0] = 0;
    if (c->image[0]) {
        wchar_t dir[MAX_PATH * 2];
        _snwprintf(dir, MAX_PATH * 2 - 1, L"%s\\\u8BC1\u636E\u56FE", g_folder);
        join_path(ip, dir, c->image);
        if (!file_exists(ip)) {
            if (file_exists(c->image)) wcscpy(ip, c->image);
            else ip[0] = 0;
        }
    }
    layout_review();
    load_image(ip, g_rImg.w - 2 * g_line, g_rImg.h - 2 * g_line);
}

static void show_case(void)
{
    if (g_n <= 0) return;
    if (g_idx < 0) g_idx = 0;
    if (g_idx >= g_n) g_idx = g_n - 1;
    Case *c = &g_cases[g_idx];
    if (g_edit && IsWindow(g_edit)) {
        wchar_t cur[STR];
        GetWindowTextW(g_edit, cur, STR);
        if (wcscmp(cur, c->user) != 0) SetWindowTextW(g_edit, c->user);
    }
    if (g_editDet && IsWindow(g_editDet)) {
        wchar_t cur[STR];
        GetWindowTextW(g_editDet, cur, STR);
        if (wcscmp(cur, c->detected) != 0) SetWindowTextW(g_editDet, c->detected);
    }
}

static void sync_user_from_edit(void)
{
    if (g_n <= 0 || !g_edit || !IsWindow(g_edit)) return;
    Case *c = &g_cases[g_idx];
    wchar_t v[STR];
    GetWindowTextW(g_edit, v, STR);
    trim(v);
    to_upper_plate(v);
    wcsncpy(c->user, v, STR - 1);
}

/*
 * The detected plate is editable so the operator can correct a misread.
 * After each edit the format rule is applied; the result drives the "format"
 * block in the left column and gates the "yes" decision, exactly like the
 * confirm field.
 */
static void sync_detected_from_edit(void)
{
    if (g_n <= 0 || !g_editDet || !IsWindow(g_editDet)) return;
    Case *c = &g_cases[g_idx];
    wchar_t v[STR];
    GetWindowTextW(g_editDet, v, STR);
    trim(v);
    to_upper_plate(v);
    wcsncpy(c->detected, v, STR - 1);

    /*
     * Do NOT touch the confirm field here.
     *
     * This function used to mirror the detected value into the confirm field
     * under a guard that read:
     *     if (!u[0] || wcscmp(u, c->user) == 0 || wcscmp(u, v) != 0)
     * The last clause fires exactly when the two differ, i.e. precisely when
     * the operator has typed a DIFFERENT plate into the confirm field - so
     * their correction was silently overwritten by the detected value.
     * Reported symptom: change the plate to a different province character,
     * press yes, and it snaps back to the detected one.
     *
     * The detected field is read-only now, so nothing needs mirroring at all.
     * The confirm field is the single source of truth for the confirmed plate.
     */
}

static void mark(int st)
{
    if (g_n <= 0) return;
    Case *c = &g_cases[g_idx];
    sync_user_from_edit();
    wchar_t v[STR];
    wcsncpy(v, c->user, STR - 1);
    trim(v);
    to_upper_plate(v);

    /*
     * The detected plate is a READ-ONLY reference value. It must never be
     * substituted for the confirmed plate: doing so silently discarded the
     * operator's correction (a corrected province character came back as the
     * detected one). Only the confirm field decides what is reported.
     */
    if (st == ST_YES) {
        if (!plate_ok(v)) {
            MessageBoxW(g_hwnd,
                L"\u786E\u8BA4\u8F66\u724C\u4E0D\u7B26\u5408\u683C\u5F0F\uFF0C"
                L"\u65E0\u6CD5\u786E\u8BA4\u4E0A\u62A5\u3002\n\n"
                L"1 \u4E2A\u6C49\u5B57 + 6 \u6216 7 \u4F4D\u5927\u5199\u82F1\u6587/\u6570\u5B57",
                L"\u683C\u5F0F\u4E0D\u7B26", MB_OK | MB_ICONWARNING);
            return;
        }
        wcsncpy(c->user, v, STR - 1);
    } else if (st == ST_NO) {
        wcsncpy(c->user, v, STR - 1);
    }
    c->st = st;

    int nxt = -1;
    for (int i = g_idx + 1; i < g_n; ++i)
        if (g_cases[i].st == ST_PENDING) { nxt = i; break; }
    if (nxt < 0)
        for (int i = 0; i < g_n; ++i)
            if (g_cases[i].st == ST_PENDING) { nxt = i; break; }
    if (nxt >= 0 && nxt != g_idx) {
        wcsncpy(g_cases[nxt].user, g_cases[nxt].user, STR - 1);
        g_idx = nxt;
        show_case_apply();
    }
    InvalidateRect(g_hwnd, NULL, FALSE);
}

static void goto_skip(void)
{
    int first = -1, after = -1;
    for (int i = 0; i < g_n; ++i) {
        if (g_cases[i].st == ST_SKIP) {
            if (first < 0) first = i;
            if (i > g_idx && after < 0) after = i;
        }
    }
    if (first < 0) {
        MessageBoxW(g_hwnd, L"\u5DF2\u5168\u90E8\u786E\u8BA4", L"\u63D0\u793A",
                    MB_OK | MB_ICONINFORMATION);
        return;
    }
    g_idx = (after >= 0) ? after : first;
    show_case_apply();
    InvalidateRect(g_hwnd, NULL, FALSE);
}

/*
 * Step 1 of "finish report": verify that every case has been decided, then ask
 * for the violation details (place + travel direction). The report itself is
 * only written once those details have been supplied - see write_report_now().
 */
static void finish_report(void)
{
    for (int i = 0; i < g_n; ++i) {
        if (g_cases[i].st == ST_PENDING) {
            MessageBoxW(g_hwnd, L"\u5C1A\u6709\u672A\u786E\u8BA4\u6848\u4F8B", L"\u63D0\u793A",
                        MB_OK | MB_ICONWARNING);
            return;
        }
    }
    sync_user_from_edit();
    open_where_dlg();
}

/* open the violation-details prompt, pre-filled with whatever was typed before */
static void open_where_dlg(void)
{
    g_wherePrev = g_screen;
    g_screen = 2;
    g_whereErr = 0;
    layout_where();
    if (g_editWhere && IsWindow(g_editWhere))
        SetWindowTextW(g_editWhere, g_where);
    place_edits();
    if (g_editWhere && IsWindow(g_editWhere)) SetFocus(g_editWhere);
    InvalidateRect(g_hwnd, NULL, TRUE);
}

static void close_where_dlg(void)
{
    g_screen = g_wherePrev;
    g_whereErr = 0;
    place_edits();
    InvalidateRect(g_hwnd, NULL, TRUE);
}

/*
 * Step 2: the details are in hand, so write the report and quit.
 *
 * The confirmed plate used here is whatever the operator left in the confirm
 * field; the detected plate is a read-only reference and is never substituted.
 */
static void write_report_now(void)
{
    sync_where_from_edit();
    if (!g_where[0]) {
        g_whereErr = 1;
        InvalidateRect(g_hwnd, NULL, FALSE);
        return;
    }
    sync_user_from_edit();

    wchar_t outdir[MAX_PATH * 2];
    _snwprintf(outdir, MAX_PATH * 2 - 1,
               L"%s\\\u4EBA\u5DE5\u5BA1\u6838\u7ED3\u679C", g_folder);
    CreateDirectoryW(outdir, NULL);

    /*
     * Step 1: file the evidence frames into each plate-named folder - the
     * highest-confidence frame plus the earliest-nearby and latest-nearby ones.
     * Done before the CSV/HTML so both can carry the same set and a folder
     * always exists for its plate.
     *
     * Best-effort: a frame that is missing only costs that one file and is
     * counted, so the report still gets produced.
     */
    g_nEvCopied = 0;
    for (int i = 0; i < g_n; ++i) {
        Case *c = &g_cases[i];
        if (c->st != ST_YES) continue;
        package_case(c, outdir, i + 1);
        g_nEvCopied += c->evCopied[0] + c->evCopied[1] + c->evCopied[2];
    }

    wchar_t csvp[MAX_PATH * 2];
    _snwprintf(csvp, MAX_PATH * 2 - 1,
               L"%s\\\u4EBA\u5DE5\u5BA1\u6838\u7ED3\u679C.csv", outdir);
    FILE *fp = open_utf8_out(csvp, 1);
    int nyes = 0, nmade = 0, nfail = 0;
    if (fp) {
        wchar_t hdr[15][STR];
        wcscpy(hdr[0], L"\u5E8F\u53F7");
        wcscpy(hdr[1], L"\u65F6\u95F4_\u8D77");
        wcscpy(hdr[2], L"\u65F6\u95F4_\u6B62");
        wcscpy(hdr[3], L"\u51FA\u73B0\u65F6\u957F_\u79D2");
        wcscpy(hdr[4], L"\u5E27\u6570");
        wcscpy(hdr[5], L"\u68C0\u6D4B\u8F66\u724C");
        wcscpy(hdr[6], L"\u7F6E\u4FE1\u5EA6");
        wcscpy(hdr[7], L"\u5E94\u6025\u8F66\u9053\u91CD\u53E0");
        wcscpy(hdr[8], L"\u8BC1\u636E\u56FE");
        wcscpy(hdr[9], L"\u7528\u6237\u786E\u8BA4");
        wcscpy(hdr[10], L"\u786E\u8BA4\u7ED3\u679C");
        /* extra frames, as the first step named them */
        wcscpy(hdr[11], COL_IMGDIR);
        wcscpy(hdr[12], COL_IMG1);
        wcscpy(hdr[13], COL_IMG2);
        wcscpy(hdr[14], COL_IMG3);
        write_utf8_line(fp, hdr, 15);

        for (int i = 0; i < g_n; ++i) {
            Case *c = &g_cases[i];
            if (c->st != ST_YES) continue;
            ++nyes;
            wchar_t row[15][STR];
            _snwprintf(row[0], STR - 1, L"%d", i + 1);
            wcsncpy(row[1], c->ts, STR - 1);
            wcsncpy(row[2], c->tsEnd, STR - 1);
            wcsncpy(row[3], c->dur, STR - 1);
            wcsncpy(row[4], c->frames, STR - 1);
            wcsncpy(row[5], c->detected, STR - 1);
            wcsncpy(row[6], c->conf, STR - 1);
            wcsncpy(row[7], c->overlap, STR - 1);
            wcsncpy(row[8], c->image, STR - 1);
            wcsncpy(row[9], c->user, STR - 1);
            wcscpy(row[10], L"\u662F");
            /* the materials list, copied as-is from the first step's CSV */
            wcsncpy(row[11], c->evDir, STR - 1);
            wcsncpy(row[12], c->evName[0], STR - 1);
            wcsncpy(row[13], c->evName[1], STR - 1);
            wcsncpy(row[14], c->evName[2], STR - 1);
            write_utf8_line(fp, row, 15);

            wchar_t cd[MAX_PATH * 2];
            _snwprintf(cd, MAX_PATH * 2 - 1, L"%s\\%s", outdir, c->user);
            if (GetFileAttributesW(cd) == INVALID_FILE_ATTRIBUTES) {
                if (CreateDirectoryW(cd, NULL)) ++nmade;
                else ++nfail;
            }
        }
        fclose(fp);
    }

    wchar_t htmlp[MAX_PATH * 2];
    _snwprintf(htmlp, MAX_PATH * 2 - 1,
               L"%s\\\u8FDD\u7AE0\u68C0\u6D4B\u62A5\u544A.html", outdir);
    write_html_report(htmlp, 0);            /* self-contained (base64) */
    wchar_t htmlq[MAX_PATH * 2];
    _snwprintf(htmlq, MAX_PATH * 2 - 1,
               L"%s\\\u8FDD\u7AE0\u68C0\u6D4B\u62A5\u544A_\u8F7B\u91CF\u7248.html",
               outdir);
    write_html_report(htmlq, 1);            /* links the files, a few KB */
    wchar_t msg[1600];
    _snwprintf(msg, 1599,
        L"\u5DF2\u751F\u6210\u62A5\u544A\uFF1A\n%s\n\n"
        L"\u786E\u8BA4\u300C\u662F\u300D\u7684\u6848\u4F8B\uFF1A%d \u6761\n"
        L"\u65B0\u5EFA\u8F66\u724C\u6587\u4EF6\u5939\uFF1A%d \u4E2A\n"
        L"\u5F52\u6863\u8BC1\u636E\u5E27\uFF1A%d \u5F20%s",
        csvp, nyes, nmade, g_nEvCopied,
        nfail ? L"\n\uFF08\u90E8\u5206\u8F66\u724C\u6587\u4EF6\u5939\u521B\u5EFA\u5931\u8D25\uFF09"
              : L"");
    MessageBoxW(g_hwnd, msg, L"\u5B8C\u6210\u62A5\u544A", MB_OK | MB_ICONINFORMATION);
    PostQuitMessage(0);
}

static void browse_folder(void)
{
    wchar_t buf[MAX_PATH * 2] = L"";
    BROWSEINFOW bi;
    memset(&bi, 0, sizeof(bi));
    bi.hwndOwner = g_hwnd;
    bi.lpszTitle = L"\u9009\u62E9\u521D\u6210\u679C\u5B58\u653E\u8DEF\u5F84";
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    LPITEMIDLIST pidl = SHBrowseForFolderW(&bi);
    if (pidl) {
        SHGetPathFromIDListW(pidl, buf);
        CoTaskMemFree(pidl);
    }
    if (buf[0]) {
        wcsncpy(g_entry, buf, MAX_PATH * 2 - 1);
        g_entryErr[0] = 0;
        InvalidateRect(g_hwnd, NULL, FALSE);
    }
}

static void do_enter(void)
{
    wchar_t p[MAX_PATH * 2];
    wcsncpy(p, g_entry, MAX_PATH * 2 - 1);
    trim(p);

    /* prefer the real edit text if present (there is none on the entry
     * screen, the folder is typed straight into our own buffer) */
    if (!p[0]) {
        wcscpy(g_entryErr, L"\u8BF7\u586B\u5199\u521D\u6210\u679C\u5B58\u653E\u8DEF\u5F84");
        InvalidateRect(g_hwnd, NULL, FALSE);
        return;
    }
    if (has_cjk(p)) {
        wcscpy(g_entryErr,
               L"\u8DEF\u5F84\u4E0D\u5F97\u5305\u542B\u4E2D\u6587");
        InvalidateRect(g_hwnd, NULL, FALSE);
        return;
    }
    if (!is_dir(p)) {
        wcscpy(g_entryErr, L"\u76EE\u5F55\u4E0D\u5B58\u5728");
        InvalidateRect(g_hwnd, NULL, FALSE);
        return;
    }
    if (load_cases(p) <= 0) {
        wcscpy(g_entryErr, L"\u672A\u627E\u5230 \u68C0\u6D4B\u7ED3\u679C.csv");
        InvalidateRect(g_hwnd, NULL, FALSE);
        return;
    }
    wcsncpy(g_folder, p, MAX_PATH * 2 - 1);
    g_entryErr[0] = 0;
    g_idx = 0;
    g_screen = 1;
    layout_review();
    place_edits();                 /* move the plate fields onto the review cell */
    show_case_apply();
    InvalidateRect(g_hwnd, NULL, TRUE);
}

/* ---------------- edit subclass: flat field, underline only ---------------- */

/*
 * Subclassed EDIT face, Minimalist Flat: plain white field with NO box; the
 * only affordance is a 2px rule under the text, which turns accent coloured
 * while the field holds focus. Both plate fields share this subclass.
 * WM_CHAR is filtered to the plate charset and the format check re-runs on
 * every keystroke.
 */
static LRESULT CALLBACK EditProc(HWND h, UINT m, WPARAM wp, LPARAM lp)
{
    WNDPROC base = DefWindowProcW;
    if (h == g_editDet) base = g_editDetOld;
    else if (h == g_editEntry) base = g_editEntryOld;
    else if (h == g_editWhere) base = g_editWhereOld;
    else if (h == g_edit) base = g_editOld;
    if (!base) base = DefWindowProcW;
    switch (m) {
    case WM_PAINT: {
        LRESULT r = CallWindowProcW(base, h, m, wp, lp);
        HDC dc = GetDC(h);
        RECT rc;
        GetClientRect(h, &rc);
        COLORREF uc = (GetFocus() == h) ? MF_ACCENT : MF_BLACK;
        fill_rect(dc, 0, rc.bottom - MF_STROKE, rc.right, MF_STROKE, uc);
        ReleaseDC(h, dc);
        return r;
    }
    case WM_ERASEBKGND: {
        HDC dc = (HDC)wp;
        RECT rc;
        GetClientRect(h, &rc);
        fill_rect(dc, 0, 0, rc.right, rc.bottom, MF_WHITE);
        return 1;
    }
    case WM_SETFOCUS:
    case WM_KILLFOCUS: {
        LRESULT r = CallWindowProcW(base, h, m, wp, lp);
        InvalidateRect(h, NULL, FALSE);        /* repaint underline colour */
        if (g_hwnd) InvalidateRect(g_hwnd, NULL, FALSE);
        return r;
    }
    case WM_CTLCOLOREDIT:
    case WM_CTLCOLORSTATIC: {
        HDC dc = (HDC)wp;
        SetTextColor(dc, MF_BLACK);
        SetBkColor(dc, MF_WHITE);
        return (LRESULT)CreateSolidBrush(MF_WHITE);
    }
    case WM_CHAR: {
        /*
         * The plate charset filter applies ONLY to the plate fields.
         *
         * The folder field must accept a full path, so restricting it to
         * [CJK A-Z 0-9] stripped every '\' and ':' out of a pasted path
         * ("D:\Users\Lhh\..." collapsed to "DUSERSLH"). Anything typed into
         * the folder field is passed through untouched.
         */
        if (h == g_editEntry) break;
        wchar_t ch = (wchar_t)wp;
        if (ch == 8 || ch == 22 || ch == 3) break;             /* BS / paste / ^C */
        if (ch < 32) break;
        if (ch >= L'a' && ch <= L'z') ch = (wchar_t)(ch - L'a' + L'A');
        BOOL okc = (ch >= L'A' && ch <= L'Z') || (ch >= L'0' && ch <= L'9') ||
                   (ch >= 0x4E00 && ch <= 0x9FFF);
        if (!okc) return 0;
        if (GetWindowTextLengthW(h) >= 8) return 0;
        wp = (WPARAM)ch;                                       /* feed uppercased */
        break;
    }
    }
    LRESULT r = CallWindowProcW(base, h, m, wp, lp);
    if (m == WM_CHAR || m == WM_KEYUP || m == WM_LBUTTONUP || m == WM_PASTE) {
        if (h == g_editDet) sync_detected_from_edit();
        else if (h == g_editEntry) sync_entry_from_edit();
        else sync_user_from_edit();
        InvalidateRect(g_hwnd, NULL, FALSE);   /* re-runs the format check */
    }
    return r;
}

/* mirror the folder field's text into g_entry so do_enter() can read it */
static void sync_entry_from_edit(void)
{
    if (!g_editEntry || !IsWindow(g_editEntry)) return;
    wchar_t v[MAX_PATH * 2];
    GetWindowTextW(g_editEntry, v, MAX_PATH * 2);
    wcsncpy(g_entry, v, MAX_PATH * 2 - 1);
    g_entry[MAX_PATH * 2 - 1] = 0;
    g_entryCaret = (int)wcslen(g_entry);
}

static HWND mk_plate_edit(WNDPROC *oldOut, BOOL readOnly)
{
    DWORD st = WS_CHILD | ES_LEFT | ES_AUTOHSCROLL;
    if (readOnly) st |= ES_READONLY;
    HWND h = CreateWindowExW(0, L"EDIT", L"", st,
                             0, 0, 10, 10, g_hwnd, NULL, g_inst, NULL);
    SendMessageW(h, WM_SETFONT, (WPARAM)g_fField, TRUE);
    *oldOut = (WNDPROC)SetWindowLongPtrW(h, GWLP_WNDPROC, (LONG_PTR)EditProc);
    return h;
}

/* folder path field on the entry screen: ordinary editable single-line text
 * (so a path copied from elsewhere can simply be pasted in) */
static HWND mk_plain_edit(WNDPROC *oldOut)
{
    HWND h = CreateWindowExW(0, L"EDIT", L"",
                             WS_CHILD | ES_LEFT | ES_AUTOHSCROLL,
                             0, 0, 10, 10, g_hwnd, NULL, g_inst, NULL);
    SendMessageW(h, WM_SETFONT, (WPARAM)g_fField, TRUE);
    *oldOut = (WNDPROC)SetWindowLongPtrW(h, GWLP_WNDPROC, (LONG_PTR)EditProc);
    return h;
}

/* violation place + heading: multiline so ~200 characters wrap and are visible */
static HWND mk_where_edit(WNDPROC *oldOut)
{
    HWND h = CreateWindowExW(0, L"EDIT", L"",
                             WS_CHILD | ES_LEFT | ES_MULTILINE | ES_AUTOVSCROLL |
                             ES_WANTRETURN | WS_VSCROLL,
                             0, 0, 10, 10, g_hwnd, NULL, g_inst, NULL);
    SendMessageW(h, WM_SETFONT, (WPARAM)g_fBody, TRUE);
    /* cap at 200 characters, including anything pasted in */
    SendMessageW(h, EM_SETLIMITTEXT, (WPARAM)200, 0);
    *oldOut = (WNDPROC)SetWindowLongPtrW(h, GWLP_WNDPROC, (LONG_PTR)EditProc);
    return h;
}

/* mirror the details box into g_where */
static void sync_where_from_edit(void)
{
    if (!g_editWhere || !IsWindow(g_editWhere)) return;
    wchar_t v[512];
    GetWindowTextW(g_editWhere, v, 512);
    wcsncpy(g_where, v, 511);
    g_where[511] = 0;
}

static void place_edits(void)
{
    const int inx = 4;
    /* the folder field lives on the entry screen only */
    if (g_editEntry && IsWindow(g_editEntry)) {
        if (g_screen == 0) {
            MoveWindow(g_editEntry, g_eField.x + inx, g_eField.y,
                       g_eField.w - 2 * inx, g_eField.h, TRUE);
            ShowWindow(g_editEntry, SW_SHOW);
        } else {
            ShowWindow(g_editEntry, SW_HIDE);
        }
    }
    /* the violation-details box lives on the prompt screen only */
    if (g_editWhere && IsWindow(g_editWhere)) {
        if (g_screen == 2) {
            MoveWindow(g_editWhere, g_wBox.x, g_wBox.y,
                       g_wBox.w, g_wBox.h, TRUE);
            ShowWindow(g_editWhere, SW_SHOW);
        } else {
            ShowWindow(g_editWhere, SW_HIDE);
        }
    }
    if (g_screen != 1) {
        if (g_editDet) ShowWindow(g_editDet, SW_HIDE);
        if (g_edit) ShowWindow(g_edit, SW_HIDE);
        return;
    }
    /* small horizontal inset so the typed text does not touch the underline
     * ends; the field face is white and borderless */
    if (g_editDet && IsWindow(g_editDet)) {
        MoveWindow(g_editDet, g_rDetEdit.x + inx, g_rDetEdit.y,
                   g_rDetEdit.w - 2 * inx, g_rDetEdit.h, TRUE);
        ShowWindow(g_editDet, SW_SHOW);
    }
    if (g_edit && IsWindow(g_edit)) {
        MoveWindow(g_edit, g_rEdit.x + inx, g_rEdit.y,
                   g_rEdit.w - 2 * inx, g_rEdit.h, TRUE);
        ShowWindow(g_edit, SW_SHOW);
    }
}

/* ---------------- window procedure ---------------- */

static LRESULT CALLBACK WndProc(HWND h, UINT m, WPARAM wp, LPARAM lp)
{
    switch (m) {
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(h, &ps);
        /* double buffered: the grid is blitted in one atomic step so no
         * half-cleared frame can show through as ghosted text */
        HDC mem = CreateCompatibleDC(dc);
        HBITMAP back = CreateCompatibleBitmap(dc, g_W, g_H);
        HGDIOBJ oldb = SelectObject(mem, back);
        if (g_screen == 1) paint_review(mem);
        else if (g_screen == 2) paint_where(mem);
        else paint_entry(mem);
        BitBlt(dc, 0, 0, g_W, g_H, mem, 0, 0, SRCCOPY);
        SelectObject(mem, oldb);
        DeleteObject(back);
        DeleteDC(mem);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_ERASEBKGND:
        return 1;
    case WM_SIZE: {
        if (wp == SIZE_MINIMIZED) return 0;
        g_W = LOWORD(lp);
        g_H = HIWORD(lp);
        layout_review();
        layout_entry();
        layout_where();
        place_edits();
        if (g_screen == 1) refit_image();
        InvalidateRect(h, NULL, FALSE);
        return 0;
    }
    case WM_GETMINMAXINFO: {
        MINMAXINFO *mmi = (MINMAXINFO *)lp;
        mmi->ptMinTrackSize.x = SC(980);
        mmi->ptMinTrackSize.y = SC(660);
        return 0;
    }

    case WM_LBUTTONDOWN: {
        int x = (int)(short)LOWORD((DWORD_PTR)lp);
        int y = (int)(short)HIWORD((DWORD_PTR)lp);
        int id = (g_screen == 1) ? hit_review(x, y)
               : (g_screen == 2) ? hit_where(x, y) : hit_entry(x, y);
        g_pressed = id;
        if (id != ID_NONE && id != ID_FIELD && id != ID_EFIELD)
            InvalidateRect(h, NULL, FALSE);
        if (g_screen == 2) {
            if (id == ID_GO) write_report_now();
            else if (id == ID_BROWSE) close_where_dlg();
            return 0;
        }
        if (g_screen == 0) {
            if (id == ID_GO) do_enter();
            else if (id == ID_BROWSE) browse_folder();
            return 0;
        }
        switch (id) {
        case ID_YES: mark(ST_YES); return 0;
        case ID_NO: mark(ST_NO); return 0;
        case ID_SKIP: mark(ST_SKIP); return 0;
        case ID_PREV:
            if (g_idx > 0) {
                sync_detected_from_edit();
                sync_user_from_edit();
                --g_idx;
                show_case_apply();
                InvalidateRect(h, NULL, FALSE);
            }
            return 0;
        case ID_NEXT: goto_skip(); return 0;
        case ID_REPORT: finish_report(); return 0;
        default: break;
        }
        /* drag to pan the image when zoomed */
        if (g_zoom > 100 && in_rect(&g_rImg, x, y)) {
            g_drag = 1;
            g_dragPt.x = x;
            g_dragPt.y = y;
            SetCapture(h);
        }
        return 0;
    }
    case WM_MOUSEMOVE: {
        int x = (int)(short)LOWORD((DWORD_PTR)lp);
        int y = (int)(short)HIWORD((DWORD_PTR)lp);
        if (g_drag) {
            g_panx += x - g_dragPt.x;
            g_pany += y - g_dragPt.y;
            g_dragPt.x = x;
            g_dragPt.y = y;
            InvalidateRect(h, NULL, FALSE);
            return 0;
        }
        {
            int id = (g_screen == 1) ? hit_review(x, y)
                   : (g_screen == 2) ? hit_where(x, y) : hit_entry(x, y);
            if (id != g_hover) {
                g_hover = id;
                /* the style inverts on hover: repaint the affected buttons.
                 * A full repaint keeps it simple and is cheap at this size. */
                InvalidateRect(h, NULL, FALSE);
            }
            /* hand-like cursor over actionable controls */
            SetCursor(LoadCursor(NULL, (id && id != ID_FIELD && id != ID_EFIELD)
                                        ? IDC_HAND : IDC_ARROW));
        }
        return 0;
    }
    case WM_LBUTTONUP:
        if (g_pressed) {
            g_pressed = 0;
            InvalidateRect(h, NULL, FALSE);
        }
        if (g_drag) { g_drag = 0; ReleaseCapture(); }
        return 0;
    case WM_LBUTTONDBLCLK:
        if (g_screen == 1) { reset_zoom(); InvalidateRect(h, NULL, FALSE); }
        return 0;
    case WM_MOUSEWHEEL: {
        if (g_screen != 1 || !g_bmp) return 0;
        int delta = GET_WHEEL_DELTA_WPARAM(wp);
        int old = g_zoom;
        int nz = g_zoom + (delta > 0 ? 25 : -25);
        if (nz < 100) nz = 100;
        if (nz > 800) nz = 800;
        if (nz != old) {
            g_zoom = nz;
            InvalidateRect(h, NULL, FALSE);
        }
        return 0;
    }

    case WM_CHAR:
        if (g_screen == 2) {
            /* the details box is a real multiline EDIT, so text goes there;
             * this branch only handles Escape to go back */
            if ((wchar_t)wp == 27) { close_where_dlg(); return 0; }
            return 0;
        }
        if (g_screen == 0) {
            /* the folder field is a real EDIT control now, so keystrokes go
             * there; this branch only handles Enter as a shortcut */
            wchar_t ch = (wchar_t)wp;
            if (ch == L'\r' || ch == L'\n') { do_enter(); return 0; }
            return 0;
        }
        break;

    case WM_DESTROY:
        free_image();
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(h, m, wp, lp);
}

#ifndef PLATEREVIEW_TEST
int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, LPWSTR cmd, int show)
{
    (void)prev; (void)cmd;
    g_inst = inst;

    Gdiplus::GdiplusStartup(&g_gdiTok, &g_gdiIn, NULL);
    CoInitialize(NULL);
    SetProcessDPIAware();
    {
        HDC dc = GetDC(NULL);
        if (dc) {
            int d = GetDeviceCaps(dc, LOGPIXELSX);
            if (d > 0) g_dpi = d;
            ReleaseDC(NULL, dc);
        }
    }
    init_fonts();

    WNDCLASSEXW wc;
    memset(&wc, 0, sizeof(wc));
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = WndProc;
    wc.hInstance = inst;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.hbrBackground = CreateSolidBrush(MF_WHITE);
    wc.lpszClassName = L"PlateReviewDeStijl";
    wc.style = CS_DBLCLKS;
    RegisterClassExW(&wc);

    /*
     * Initial window size.
     *
     * Size in device-independent units, then clamp hard against BOTH the work
     * area and the primary screen extent. Reported bug this fixes: on a 200%
     * display (physical 3072x1920) the earlier "fraction of the screen" rule
     * produced a 2720x1760 window whose bottom edge sat below the visible
     * desktop, so controls low on the screen could not even be reached by the
     * mouse (the cursor clamps to the screen extent).
     */
    RECT wa;
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &wa, 0);
    int sw = GetSystemMetrics(SM_CXSCREEN);
    int sh = GetSystemMetrics(SM_CYSCREEN);
    int availw = wa.right - wa.left;
    int availh = wa.bottom - wa.top;
    if (availw <= 0 || availw > sw) availw = sw;
    if (availh <= 0 || availh > sh) availh = sh;

    int maxW = (int)(availw * 0.90);
    int maxH = (int)(availh * 0.90);

    int W = SC(1200), H = SC(820);
    if (W > maxW) W = maxW;
    if (H > maxH) H = maxH;
    if (W < SC(900)) W = SC(900);
    if (H < SC(600)) H = SC(600);
    if (W > maxW) W = maxW;
    if (H > maxH) H = maxH;
    if (W < 640) W = 640;
    if (H < 480) H = 480;

    g_hwnd = CreateWindowExW(0, L"PlateReviewDeStijl",
                             L"\u8F66\u724C\u5BA1\u6838  PLATE REVIEW",
                             WS_OVERLAPPEDWINDOW,
                             wa.left + (availw - W) / 2,
                             wa.top + (availh - H) / 2, W, H,
                             NULL, NULL, inst, NULL);
    if (!g_hwnd) return 1;

    RECT rc;
    GetClientRect(g_hwnd, &rc);
    g_W = rc.right;
    g_H = rc.bottom;
    layout_review();
    g_editDet = mk_plate_edit(&g_editDetOld, TRUE);   /* detected plate: read-only */
    g_edit = mk_plate_edit(&g_editOld, FALSE);        /* confirm plate: editable */
    g_editEntry = mk_plain_edit(&g_editEntryOld);     /* folder path: editable/paste */
    g_editWhere = mk_where_edit(&g_editWhereOld);     /* place + heading: multiline */
    place_edits();                   /* shows the folder field, hides the others */
    SetFocus(g_editEntry);

    ShowWindow(g_hwnd, show);
    UpdateWindow(g_hwnd);
    SetFocus(g_hwnd);          /* entry screen reads WM_CHAR itself */

    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    Gdiplus::GdiplusShutdown(g_gdiTok);
    CoUninitialize();
    return 0;
}
#endif /* PLATEREVIEW_TEST */

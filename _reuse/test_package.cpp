/* End-to-end check of the "generate report" step on a REAL initial-result folder.
 *
 * Why this exists: the GUI's own input boxes cannot be driven from another
 * process (SetWindowTextW / WM_SETTEXT / WM_CHAR all fail across processes -
 * see the hand-over doc, section 0.3), so "press the buttons" is not
 * automatable. What IS automatable is everything behind the buttons, and that
 * is where the evidence packaging lives: read 检测结果.csv, copy the three
 * evidence frames into each plate-named folder, and write the report.
 *
 * This harness calls write_report_now() on a folder that the real pipeline
 * produced, then asserts on the artefacts. Run it with:
 *
 *     dist\test_package.exe <initial-result-folder>
 *
 * It DOES call write_report_now(), which takes down the message box with a timer
 * and then quits - exactly like the GUI would - so never point it at a folder
 * whose 人工审核结果 you still need: it regenerates that folder in place.
 */
#include "PlateReview.cpp"

#include <direct.h>

static int failures = 0;
static int checks = 0;

static void ck(const char *what, int ok, const char *detail)
{
    ++checks;
    if (!ok) ++failures;
    printf("  [%s] %s%s%s\n", ok ? "PASS" : "FAIL", what,
           (detail && detail[0]) ? "  --  " : "", detail ? detail : "");
}

static long fsz(const wchar_t *p)
{
    FILE *f = _wfopen(p, L"rb");
    if (!f) return -1;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fclose(f);
    return n;
}

/* UTF-8 helper: the console here is not UTF-8, so print Chinese paths via this. */
static void w2u(const wchar_t *w, char *out, int cch)
{
    int r = WideCharToMultiByte(CP_UTF8, 0, w, -1, out, cch - 1, NULL, NULL);
    if (r <= 0) { out[0] = 0; return; }
    out[r] = 0;
}

static int has(const wchar_t *p, const char *needle)
{
    FILE *f = _wfopen(p, L"rb");
    if (!f) return 0;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n <= 0) { fclose(f); return 0; }
    char *b = (char *)malloc((size_t)n + 1);
    if (!b) { fclose(f); return 0; }
    size_t rd = fread(b, 1, (size_t)n, f);
    fclose(f);
    b[rd] = 0;
    int hit = strstr(b, needle) != NULL;
    free(b);
    return hit;
}

/* The GUI installs this in wWinMain; here we just close the box immediately. */
static DWORD WINAPI closer(LPVOID)
{
    for (int i = 0; i < 400; ++i) {
        HWND h = FindWindowW(NULL, L"\u5B8C\u6210\u62A5\u544A");
        if (h) { PostMessageW(h, WM_CLOSE, 0, 0); return 0; }
        Sleep(25);
    }
    return 0;
}

int main(int argc, char **argv)
{
    printf("============================================================\n");
    printf("Evidence packaging on a real initial-result folder\n");
    printf("============================================================\n");
    if (argc < 2) {
        printf("usage: test_package.exe <initial-result-folder>\n");
        return 2;
    }
    {
        wchar_t wfolder[MAX_PATH * 2];
        utf8_to_w(argv[1], wfolder, (int)ARRAYSZ(wfolder));
        /*
         * Normalise to an absolute path. A relative path would otherwise be
         * resolved against the current directory, which is whatever folder the
         * caller happened to be in - measured: "downloads\xt_v020" from the
         * repo root turned into a folder that does not exist, and load_cases
         * silently returned 0.
         */
        wchar_t full[MAX_PATH * 2];
        DWORD got = GetFullPathNameW(wfolder, (DWORD)ARRAYSZ(full), full, NULL);
        if (got > 0 && got < ARRAYSZ(full)) wcscpy(wfolder, full);
        wcsncpy(g_folder, wfolder, MAX_PATH * 2 - 1);
    }
    printf("folder: %ls\n", g_folder);

    int n = load_cases(g_folder);
    char d[128];
    _snprintf(d, sizeof(d) - 1, "g_n=%d", n);
    ck("load_cases reads the pipeline CSV", n >= 1, d);
    if (n < 1) return 1;

    /* how many cases actually carry the two extra frames */
    int withAlt = 0, withAlt3 = 0;
    for (int i = 0; i < n; ++i) {
        if (g_cases[i].evName[1][0]) ++withAlt;
        if (g_cases[i].evName[2][0]) ++withAlt3;
    }
    printf("  (extra frames present: earliest %d/%d, latest %d/%d)\n",
           withAlt, n, withAlt3, n);
    ck("the earliest-nearby column is used at all", withAlt > 0, "");

    /* confirm every case, as an operator pressing 是 would */
    for (int i = 0; i < n; ++i) g_cases[i].st = ST_YES;
    wcscpy(g_where, L"\u6D4B\u8BD5\u5730\u70B9\u4E0E\u65B9\u5411");
    ck("details length within the 200-char rule", wcslen(g_where) <= 200, "");

    CreateThread(NULL, 0, closer, NULL, 0, NULL);
    write_report_now();

    wchar_t outdir[MAX_PATH * 2];
    _snwprintf(outdir, MAX_PATH * 2 - 1,
               L"%s\\\u4EBA\u5DE5\u5BA1\u6838\u7ED3\u679C", g_folder);

    printf("\nartefacts:\n");
    int withPrimary = 0, with2 = 0, with3 = 0;
    for (int i = 0; i < n; ++i) {
        Case *c = &g_cases[i];
        wchar_t pdir[MAX_PATH * 2], f1[MAX_PATH * 2], f2[MAX_PATH * 2], f3[MAX_PATH * 2];
        char pu[256];
        _snwprintf(pdir, MAX_PATH * 2 - 1, L"%s\\%s", outdir, c->user);
        _snwprintf(f1, MAX_PATH * 2 - 1, L"%s\\%02d_\u539F\u56FE.jpg", pdir, i + 1);
        _snwprintf(f2, MAX_PATH * 2 - 1, L"%s\\%02d_\u8BC1\u636E\u5E272.jpg", pdir, i + 1);
        _snwprintf(f3, MAX_PATH * 2 - 1, L"%s\\%02d_\u8BC1\u636E\u5E273.jpg", pdir, i + 1);
        if (fsz(f1) > 0) ++withPrimary;
        if (fsz(f2) > 0) ++with2;
        if (fsz(f3) > 0) ++with3;
        w2u(c->user, pu, sizeof(pu));
        printf("  %-12s primary=%ldB  earliest=%ldB  latest=%ldB\n",
               pu, fsz(f1), fsz(f2), fsz(f3));
    }

    ck("every confirmed case has its primary evidence frame", withPrimary == n, "");
    ck("every case that had an earliest frame got it filed", with2 == withAlt, "");
    ck("every case that had a latest frame got it filed", with3 == withAlt3, "");

    wchar_t html[MAX_PATH * 2];
    _snwprintf(html, MAX_PATH * 2 - 1,
               L"%s\\\u8FDD\u7AE0\u68C0\u6D4B\u62A5\u544A.html", outdir);
    ck("report written", fsz(html) > 2000, "");
    ck("report inlines the evidence images", has(html, "data:image/jpeg;base64,"), "");
    /* UTF-8 of the two captions: "2 最早附近帧" and "3 最晚附近帧" */
    if (withAlt > 0)
        ck("report shows the earliest-nearby frame caption",
           has(html, "\xE6\x9C\x80\xE6\x97\xA9\xE9\x99\x84\xE8\xBF\x91"), "");
    if (withAlt3 > 0)
        ck("report shows the latest-nearby frame caption",
           has(html, "\xE6\x9C\x80\xE6\x99\x9A\xE9\x99\x84\xE8\xBF\x91"), "");
    ck("report no longer mentions violation clips",
       !has(html, "\u8FDD\u7AE0\u89C6\u9891\u7247\u6BB5") && !has(html, ".mp4"), "");

    wchar_t csvp[MAX_PATH * 2];
    _snwprintf(csvp, MAX_PATH * 2 - 1,
               L"%s\\\u4EBA\u5DE5\u5BA1\u6838\u7ED3\u679C.csv", outdir);
    ck("review CSV written", fsz(csvp) > 100, "");
    /* UTF-8 of "证据图2" / "证据图3" (split the literal: a hex escape would
     * otherwise swallow the trailing digit) */
    ck("review CSV carries the extra evidence columns",
       has(csvp, "\xE8\xAF\x81\xE6\x8D\xAE\xE5\x9B\xBE" "2") &&
       has(csvp, "\xE8\xAF\x81\xE6\x8D\xAE\xE5\x9B\xBE" "3"), "");
    ck("review CSV no longer carries a clip column", !has(csvp, ".mp4"), "");

    printf("\n============================================================\n");
    printf("RESULT: %s (%d checks, %d failed)\n",
           failures ? "FAILED" : "ALL PASSED", checks, failures);
    printf("============================================================\n");
    return failures ? 1 : 0;
}

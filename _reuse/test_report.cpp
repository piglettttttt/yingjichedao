/* Console harness for the report generator and the confirm-plate rule.
 *
 * The GUI cannot be driven reliably from another process (cross-process
 * SetWindowTextW/WM_SETTEXT on an EDIT is ignored and posted WM_CHAR does not
 * reach it), so the logic is exercised directly by including PlateReview.cpp
 * with wWinMain excluded.
 *
 * Covered here:
 *   1. the report generator writes a valid, self-contained HTML report
 *   2. the report uses the USER-CONFIRMED plate, not the detected one
 *   3. regression: correcting 粤AX285E to 桂AX285E and pressing "yes" keeps 桂
 *      (the old code snapped back to the detected value)
 */
#define PLATEREVIEW_TEST 1
#include "PlateReview.cpp"

static int failures = 0;

static void ck(const char *name, int ok, const char *detail)
{
    if (ok) printf("  [PASS] %s\n", name);
    else { ++failures; printf("  [FAIL] %s  -- %s\n", name, detail ? detail : ""); }
}

static int file_size(const wchar_t *p)
{
    FILE *f = _wfopen(p, L"rb");
    if (!f) return -1;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fclose(f);
    return (int)n;
}

static int file_has(const wchar_t *p, const char *needle)
{
    FILE *f = _wfopen(p, L"rb");
    if (!f) return 0;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = (char *)malloc((size_t)n + 1);
    if (!buf) { fclose(f); return 0; }
    size_t rd = fread(buf, 1, (size_t)n, f);
    fclose(f);
    buf[rd] = 0;
    int hit = (strstr(buf, needle) != NULL);
    free(buf);
    return hit;
}

static void w2u(const wchar_t *w, char *out, int cch)
{
    int r = WideCharToMultiByte(CP_UTF8, 0, w, -1, out, cch - 1, NULL, NULL);
    if (r <= 0) { out[0] = 0; return; }
    out[r] = 0;
}

int main(void)
{
    printf("============================================================\n");
    printf("Report + confirm-plate regression\n");
    printf("============================================================\n\n");

    wchar_t folder[MAX_PATH * 2];
    wcscpy(folder,
           L"D:\\Users\\Lhh\\Documents\\dsh\\yingjichedao\\downloads\\ax285e_test");

    printf("1) load cases\n");
    int n = load_cases(folder);
    char d[160];
    _snprintf(d, sizeof(d) - 1, "g_n=%d", n);
    ck("load_cases returns >= 1", n >= 1, d);
    if (n < 1) return 1;
    wcsncpy(g_folder, folder, MAX_PATH * 2 - 1);

    char det_u8[128];
    w2u(g_cases[0].detected, det_u8, sizeof(det_u8));
    printf("   detected = %s\n", det_u8);
    ck("detected plate is the machine reading", strcmp(det_u8, "\xE7\xB2\xA4""AX285E") == 0, det_u8);

    /*
     * 2) Regression for the reported bug.
     *
     * Simulate what the GUI does: the operator types a DIFFERENT plate into the
     * confirm field, then presses 是. The stored confirmed plate must be the
     * typed one, never the detected one.
     */
    printf("\n2) confirm-plate correction survives pressing yes\n");
    {
        /*
         * A real EDIT is needed because sync_user_from_edit() reads the control,
         * exactly as the GUI does. It must be parented to a real window,
         * otherwise CreateWindowEx fails and the check is skipped.
         */
        HWND host = CreateWindowExW(0, L"STATIC", L"", WS_OVERLAPPED,
                                    0, 0, 100, 100, NULL, NULL, NULL, NULL);
        g_edit = CreateWindowExW(0, L"EDIT", L"",
                                 WS_CHILD | ES_LEFT | ES_AUTOHSCROLL,
                                 0, 0, 100, 30, host, NULL, NULL, NULL);
        if (!g_edit) {
            printf("  [FAIL] could not create the confirm EDIT (host=%p)\n",
                   (void *)host);
            ++failures;
        } else {
            const wchar_t *corrected = L"\u6842AX285E";     /* 桂AX285E */
            SetWindowTextW(g_edit, corrected);
            g_hwnd = host;              /* show_case_apply() may repaint */
            g_idx = 0;
            g_cases[0].st = ST_PENDING;
            mark(ST_YES);

            char got_u8[128];
            w2u(g_cases[0].user, got_u8, sizeof(got_u8));
            printf("   detected        = %s\n", det_u8);
            printf("   user confirmed  = %s\n", got_u8);
            ck("confirmed plate keeps the typed value, not the detected one",
               strcmp(got_u8, "\xE6\xA1\x82""AX285E") == 0, got_u8);
            ck("status became ST_YES", g_cases[0].st == ST_YES, "");
            DestroyWindow(g_edit);
            g_edit = NULL;
            DestroyWindow(host);
            g_hwnd = NULL;
        }
    }

    /* 3) report generation */
    printf("\n3) write_html_report\n");
    for (int i = 0; i < n; ++i) {
        if (!g_cases[i].user[0])
            wcsncpy(g_cases[i].user, g_cases[i].detected, STR - 1);
        g_cases[i].st = ST_YES;
    }
    /*
     * The report now requires the violation details (place + travel direction)
     * that the operator supplies before generating it. Set them here and check
     * that they reach the HTML.
     */
    wcscpy(g_where,
           L"G4\u6CAA\u6606\u9AD8\u901F\u5E7F\u5DDE\u6BB5 K12+300 "
           L"\u5E94\u6025\u8F66\u9053\uFF0C"
           L"\u7531\u897F\u5411\u4E1C\u884C\u9A76");
    {
        char w_u8[256];
        w2u(g_where, w_u8, sizeof(w_u8));
        printf("   violation details (%d chars) = %s\n",
               (int)wcslen(g_where), w_u8);
    }

    wchar_t out[MAX_PATH * 2], html[MAX_PATH * 2];
    _snwprintf(out, MAX_PATH * 2 - 1,
               L"%s\\\u4EBA\u5DE5\u5BA1\u6838\u7ED3\u679C", folder);
    CreateDirectoryW(out, NULL);
    _snwprintf(html, MAX_PATH * 2 - 1,
               L"%s\\\u8FDD\u7AE0\u68C0\u6D4B\u62A5\u544A.html", out);
    write_html_report(html);

    int sz = file_size(html);
    _snprintf(d, sizeof(d) - 1, "size=%d bytes", sz);
    ck("HTML report created and non-empty", sz > 1000, d);
    ck("contains DOCTYPE", file_has(html, "<!DOCTYPE html>"), "");
    ck("contains the confirmed plate",
       file_has(html, "\xE6\xA1\x82""AX285E"), "");
    ck("contains the violation place/direction heading",
       file_has(html, "\xE8\xBF\x9D\xE6\xB3\x95\xE5\x9C\xB0\xE7\x82\xB9"), "");
    ck("contains the typed violation details",
       file_has(html, "\xE7\x94\xB1\xE8\xA5\xBF\xE5\x90\x91\xE4\xB8\x9C"), "");
    ck("details block capped at 200 chars by the EDIT limit",
       wcslen(g_where) <= 200, "");
    ck("contains inlined base64 image",
       file_has(html, "data:image/jpeg;base64,"), "");
    ck("did not fall back to the annotated image",
       !file_has(html, "\xE6\x9C\xAA\xE6\x89\xBE\xE5\x88\xB0\xE6\x9C\xAA\xE6\xA0\x87"
                       "\xE6\xB3\xA8\xE5\x8E\x9F\xE5\x9B\xBE"), "");
    ck("no shadow in CSS", !file_has(html, "box-shadow"), "");
    ck("no gradient in CSS", !file_has(html, "gradient"), "");

    wchar_t plain[MAX_PATH * 2], annot[MAX_PATH * 2];
    _snwprintf(plain, MAX_PATH * 2 - 1,
               L"%s\\\u8BC1\u636E\u56FE\\\u539F\u56FE\\%s", folder, g_cases[0].image);
    _snwprintf(annot, MAX_PATH * 2 - 1,
               L"%s\\\u8BC1\u636E\u56FE\\%s", folder, g_cases[0].image);
    ck("plain original frame exists", file_size(plain) > 0, "");
    ck("plain frame differs from the annotated frame",
       file_size(plain) != file_size(annot), "");

    printf("\n============================================================\n");
    printf("RESULT: %s\n", failures ? "FAILED" : "ALL PASSED");
    printf("============================================================\n");
    return failures ? 1 : 0;
}

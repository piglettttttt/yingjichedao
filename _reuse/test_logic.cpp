/*
 * Console harness: verifies PlateReview's core logic without the GUI.
 *
 * Compiled with -DPLATEREVIEW_TEST so PlateReview.cpp's wWinMain is excluded;
 * this file supplies main().
 *
 * Checks:
 *   1. plate format rule (1 CJK + 6/7 of [A-Z0-9])
 *   2. load_cases() parses the CSV correctly (UTF-8 BOM, column lookup)
 *   3. write_report_now() writes only the "yes" cases + creates plate folders
 */
#define PLATEREVIEW_TEST 1
#include "PlateReview.cpp"

#include <direct.h>

static int g_pass = 0, g_fail = 0;

static void ck(const char *name, int ok, const char *detail)
{
    if (ok) { ++g_pass; printf("  [PASS] %s\n", name); }
    else { ++g_fail; printf("  [FAIL] %s  -- %s\n", name, detail ? detail : ""); }
}

static void ck_plate(const wchar_t *in, int expect)
{
    int got = plate_ok(in) ? 1 : 0;
    char nb[256];
    WideCharToMultiByte(CP_UTF8, 0, in, -1, nb, sizeof(nb), NULL, NULL);
    char msg[320];
    _snprintf(msg, sizeof(msg) - 1, "plate_ok('%s')=%d expect=%d", nb, got, expect);
    ck(msg, got == expect, "");
}

/* 生成测试用初成果目录 */
static void make_fixture(const wchar_t *dir)
{
    CreateDirectoryW(dir, NULL);
    wchar_t sub[MAX_PATH * 2];
    _snwprintf(sub, MAX_PATH * 2 - 1, L"%s\\\u8BC1\u636E\u56FE", dir);
    CreateDirectoryW(sub, NULL);

    wchar_t csvp[MAX_PATH * 2];
    _snwprintf(csvp, MAX_PATH * 2 - 1, L"%s\\\u68C0\u6D4B\u7ED3\u679C.csv", dir);
    FILE *fp = open_utf8_out(csvp, 1);
    if (!fp) return;
    const char *body =
        "1,2023-03-04 15:38:03,2023-03-04 15:38:05,2.07,30,\xE7\xB2\xA4""AAR902,0.86,"
        "SUSPECT,1.0,ok.jpg,\n"
        "2,2023-03-04 15:38:14,2023-03-04 15:38:17,3.13,48,\xE7\xB2\xA4""BVV600,0.81,"
        "SUSPECT,1.0,ok.jpg,\n"
        "3,2023-03-04 15:38:25,2023-03-04 15:38:27,1.80,28,\xE6\xA1\x82""A7M7M4,0.92,"
        "SUSPECT,1.0,ok.jpg,\n";
    wchar_t hdr[11][STR];
    wcscpy(hdr[0], COL_TS);      /* 用与程序一致的列名常量，避免手打错 */
    /* 表头按程序期望的字段顺序写 */
    fclose(fp);

    /* 重新按正确表头写 */
    fp = open_utf8_out(csvp, 1);
    if (!fp) return;
    wchar_t H[11][STR];
    wcscpy(H[0], L"\u5E8F\u53F7");
    wcscpy(H[1], COL_TS);
    wcscpy(H[2], COL_END);
    wcscpy(H[3], COL_DUR);
    wcscpy(H[4], COL_FR);
    wcscpy(H[5], COL_PLATE);
    wcscpy(H[6], COL_CONF);
    wcscpy(H[7], L"\u72B6\u6001");
    wcscpy(H[8], COL_OV);
    wcscpy(H[9], COL_IMG);
    wcscpy(H[10], L"\u7528\u6237\u786E\u8BA4");
    write_utf8_line(fp, H, 11);
    fwrite(body, 1, strlen(body), fp);
    fclose(fp);
}

int main(void)
{
    printf("==============================================================\n");
    printf("PlateReview core logic self-test\n");
    printf("==============================================================\n");

    /* ---- 1. plate format ---- */
    printf("\n1) plate format: 1 CJK + 6 or 7 of [A-Z0-9]\n");
    ck_plate(L"\u7CA4AAR902", 1);        /* 粤AAR902  -> 6 */
    ck_plate(L"\u7CA4A1G0V91", 1);       /* 粤A1G0V91 -> 7 */
    ck_plate(L"\u6842A7M7M4", 1);        /* 桂A7M7M4  -> 6 */
    ck_plate(L"\u7CA4A", 0);             /* too short */
    ck_plate(L"\u7CA4A1G0V911", 0);      /* too long (8) */
    ck_plate(L"AAR902", 0);              /* no CJK */
    ck_plate(L"1234567", 0);             /* no CJK */
    ck_plate(L"\u7CA4A1G0V9-", 0);       /* illegal char */
    ck_plate(L"\u7CA4A1G0V9!", 0);       /* illegal char */
    ck_plate(L"", 0);                    /* empty */

    /* upper-casing */
    {
        wchar_t t[STR];
        wcscpy(t, L"\u7CA4a1g0v91");
        to_upper_plate(t);
        ck("to_upper_plate lowercases -> upper",
           wcscmp(t, L"\u7CA4A1G0V91") == 0, "");
    }

    /* ---- 2. CJK detection for path validation ---- */
    printf("\n2) CJK path detection\n");
    ck("has_cjk('C:\\\\test\\\\a') == 0", has_cjk(L"C:\\test\\a") == 0, "");
    ck("has_cjk('C:\\\\\u4E2D\u6587') == 1", has_cjk(L"C:\\\u4E2D\u6587") == 1, "");

    /* ---- 3. load_cases ---- */
    printf("\n3) load_cases() CSV parsing\n");
    wchar_t fix[MAX_PATH * 2];
    const wchar_t *base = L"C:\\Users\\Lhh\\pr_test2";
    wcscpy(fix, base);
    /* 清理旧目录 */
    {
        wchar_t cmd[MAX_PATH * 3];
        _snwprintf(cmd, MAX_PATH * 3 - 1, L"cmd /c rmdir /s /q \"%s\"", fix);
        _wsystem(cmd);
    }
    make_fixture(fix);
    int n = load_cases(fix);
    char d[128];
    _snprintf(d, sizeof(d) - 1, "g_n=%d", n);
    ck("load_cases returns 3", n == 3, d);
    if (n == 3) {
        char b1[256];
        WideCharToMultiByte(CP_UTF8, 0, g_cases[0].detected, -1, b1, sizeof(b1), NULL, NULL);
        ck("case1 detected == 粤AAR902", wcscmp(g_cases[0].detected, L"\u7CA4AAR902") == 0, b1);
        ck("case1 user defaults to detected",
           wcscmp(g_cases[0].user, g_cases[0].detected) == 0, "");
        ck("case1 status pending", g_cases[0].st == ST_PENDING, "");
        ck("case1 ts parsed",
           wcscmp(g_cases[0].ts, L"2023-03-04 15:38:03") == 0, "");
        ck("case3 detected == 桂A7M7M4",
           wcscmp(g_cases[2].detected, L"\u6842A7M7M4") == 0, "");
    }

    /* ---- 4. write_report_now ---- */
    printf("\n4) write_report_now(): only 'yes' cases + plate folders\n");
    if (n == 3) {
        /*
         * "finish report" is now two steps: finish_report() validates every
         * case and opens the violation-details prompt, then write_report_now()
         * writes the outputs once the place/heading have been supplied. The
         * details are mandatory, so set them here before writing.
         */
        wcsncpy(g_folder, fix, MAX_PATH * 2 - 1);
        g_cases[0].st = ST_YES;   wcscpy(g_cases[0].user, L"\u7CA4AAR902");
        g_cases[1].st = ST_NO;
        g_cases[2].st = ST_YES;   wcscpy(g_cases[2].user, L"\u6842A7M7M4");
        wcscpy(g_where,
               L"G4\u6CAA\u6606\u9AD8\u901F K12+300 \u5E94\u6025\u8F66\u9053\uFF0C"
               L"\u7531\u897F\u5411\u4E1C");
        write_report_now();

        wchar_t outdir[MAX_PATH * 2];
        _snwprintf(outdir, MAX_PATH * 2 - 1, L"%s\\\u4EBA\u5DE5\u5BA1\u6838\u7ED3\u679C", fix);
        ck("created 人工审核结果 dir", is_dir(outdir) == 1, "");

        wchar_t csvp[MAX_PATH * 2];
        _snwprintf(csvp, MAX_PATH * 2 - 1,
                   L"%s\\\u4EBA\u5DE5\u5BA1\u6838\u7ED3\u679C.csv", outdir);
        ck("created 人工审核结果.csv", file_exists(csvp) == 1, "");

        wchar_t f1[MAX_PATH * 2], f2[MAX_PATH * 2], f3[MAX_PATH * 2];
        _snwprintf(f1, MAX_PATH * 2 - 1, L"%s\\\u7CA4AAR902", outdir);
        _snwprintf(f2, MAX_PATH * 2 - 1, L"%s\\\u6842A7M7M4", outdir);
        _snwprintf(f3, MAX_PATH * 2 - 1, L"%s\\\u7CA4BVV600", outdir);
        ck("created folder for 粤AAR902 (yes)", is_dir(f1) == 1, "");
        ck("created folder for 桂A7M7M4 (yes)", is_dir(f2) == 1, "");
        ck("NOT created folder for 粤BVV600 (no)", is_dir(f3) == 0, "");

        /* 读回 CSV 校验行数 = 表头 + 2 */
        FILE *fp = _wfopen(csvp, L"rb");
        if (fp) {
            char buf[8192];
            size_t rd = fread(buf, 1, sizeof(buf) - 1, fp);
            buf[rd] = 0;
            fclose(fp);
            int lines = 0;
            for (size_t i = 0; i < rd; ++i) if (buf[i] == '\n') ++lines;
            char dl[128];
            _snprintf(dl, sizeof(dl) - 1, "lines=%d", lines);
            ck("CSV has header + 2 yes-rows", lines == 3, dl);
            ck("CSV has UTF-8 BOM",
               rd >= 3 && (unsigned char)buf[0] == 0xEF &&
               (unsigned char)buf[1] == 0xBB && (unsigned char)buf[2] == 0xBF, "");
            ck("CSV does not contain the 'no' plate",
               strstr(buf, "\xE7\xB2\xA4""BVV600") == NULL, "");
        }
    }

    printf("\n==============================================================\n");
    printf("RESULT: %d passed, %d failed\n", g_pass, g_fail);
    printf("==============================================================\n");
    return g_fail == 0 ? 0 : 1;
}

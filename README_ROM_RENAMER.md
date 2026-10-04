# ROM Japanese Renamer v1.8.0

No-Intro / Redump 等のDAT照合とGeminiを使い、**日本版ROMだけ**を日本の公式タイトルへ安全にリネームするGUIアプリです。

## v1.8.0 の主な変更

海外ROMを日本語タイトルへリネームしないため、地域フィルタを追加しました。

GUIの既定値:

```text
☑ 日本版ROMのみ日本語化（海外/地域不明を除外）
```

この設定がONの場合、明示的に日本版と確認できたROMだけがGeminiへの問い合わせ・リネーム対象になります。

## 地域判定

地域判定は次の優先順位です。

1. DAT一致タイトル
2. DAT内ROM名
3. 元ファイル名

DATのハッシュ一致情報をファイル名より優先します。

例:

```text
ファイル名: Game (J).zip
DAT一致:   Game (USA)
→ 海外ROMとして除外
```

逆に:

```text
ファイル名: Game (U).zip
DAT一致:   Game (Japan)
→ 日本版として処理
```

## 日本版として処理する例

```text
Game (Japan).sfc
Game (J).smc
Game (JU) [!].smc
Game (JUE).sfc
Game (Japan, USA).sfc
```

`Japan` を含むマルチリージョンROMは「日本含む」として処理対象です。

## 海外ROMとして除外する例

```text
Game (USA).sfc
Game (U).sfc
Game (Europe).sfc
Game (E).sfc
Game (France).sfc
Game (Germany).sfc
Game (World).sfc
Game (Korea).sfc
Game (China).sfc
```

一覧の状態は:

```text
海外ROM除外
```

となり、日本語タイトル・変更後ファイル名は作成されません。

## 地域不明ROM

たとえば:

```text
15 Puzzle (PD).zip
Some Homebrew.zip
Unknown Game.zip
```

のように、日本版である根拠がないものは厳格モードでは:

```text
地域不明除外
```

となります。

これは海外ROMを誤って日本語化しないため、安全側の仕様です。

必要な場合はGUIの:

```text
日本版ROMのみ日本語化（海外/地域不明を除外）
```

をOFFにすれば従来どおり候補化できます。

## 一覧表示

v1.8.0では「地域」列を追加しました。

表示例:

```text
地域       状態
--------------------------
日本       候補
日本含む   候補
海外       海外ROM除外
不明       地域不明除外
```

## Gemini API使用量

海外ROM・地域不明ROMはGeminiへ送信しません。

そのため、大量の海外ROMが混ざったセットではAPI使用量も削減できます。

## 旧キャッシュについて

v1.7.0以前に海外ROMを日本語化していたGeminiキャッシュが残っていても問題ありません。

日本版限定モードでは:

```text
海外ROM
↓
地域フィルタで除外
↓
旧Geminiキャッシュを使用しない
↓
日本語化しない
```

となります。

キャッシュ自体は削除しないため、日本版ROMの既存チェック結果はそのまま再利用できます。

## 旧CSVについて

v1.7.0以前のCSVも読み込めます。

読み込み時に地域判定を再実行し、海外ROM・地域不明ROMは自動的に除外します。
旧CSVに日本語タイトルが保存されていても、日本版限定モードではその日本語タイトル/変更後ファイル名をクリアし、適用しません。

## 完了フォルダ移動

海外ROM・地域不明ROMは:

- リネーム対象外
- 完了フォルダ移動対象外

です。

「リネーム済みを完了へ移動」についても、日本版限定モードでは日本版と確認できた履歴だけを移動します。

旧履歴しかない場合は、リネーム前の元ファイル名に `(Japan)` / `(J)` 等が明示されているものだけを安全側で採用します。

## 既存機能

v1.7.0までの機能は維持しています。

- DATマネージャー
- No-Intro / Redump / MAME / TOSEC
- Gemini APIキーをWindows資格情報マネージャーへ保存
- メインROMのみ
- Bad Dump / Hack / Beta / Proto等の除外
- サブフォルダ検索
- `SFC` → `SFC完了` のような完了フォルダ移動
- 既にリネーム済みROMの後追い移動
- CSV保存/読込
- Undo
- 既存Geminiキャッシュ再利用

## 起動

```powershell
py -m pip install -r requirements.txt
py rom_renamer_gui.py
```

または:

```text
run_rom_renamer.cmd
```

## EXE化

```powershell
.\build_exe.ps1
```

生成先:

```text
dist\ROM-Japanese-Renamer.exe
```

## v1.8.0 テスト済み

- `(J)` → 日本
- `(Japan)` → 日本
- `(JU)` / `(JUE)` → 日本含む
- `(Japan, USA)` → 日本含む
- `(U)` / `(USA)` → 海外
- `(E)` / `(Europe)` → 海外
- `(France)` / `(Germany)` → 海外
- `(World)` → 海外
- `(PD)` → 地域不明
- DAT地域情報をファイル名より優先
- 海外ROMはGeminiへ送信しない
- 旧CSVの海外日本語タイトルを適用しない
- 旧履歴からの海外ROM完了移動を防止
- Python構文チェック
- ZIP整合性チェック

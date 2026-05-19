# /pr — Commit · Push · PR

Mevcut branch'teki değişiklikleri commit'ler, remote'a push'lar ve GitHub PR'ı açar.

## Kullanım

```
/pr                    → değişiklikleri analiz et, commit mesajı öner, onay al
/pr "feat: açıklama"   → commit mesajı hazır, doğrudan ilerle
```

## Talimatlar

### 1. Ön kontrol

```bash
git branch --show-current
git status
git diff
git log --oneline -5
```

- `main` branch'indeyse dur: "main'de PR açılmaz. Önce feature branch aç."
- Hiç değişiklik yoksa dur: "Commit edilecek değişiklik yok."

### 2. Commit mesajı belirle

Argüman verilmişse onu kullan. Verilmemişse diff'i analiz et ve öner:
- Format: `<type>(<scope>): <özet>` — Türkçe özet kabul edilir
- type: `feat` / `fix` / `refactor` / `docs` / `chore`
- scope: modül kodu (`m05`, `m01`) veya alan (`db`, `pipeline`)

`AskUserQuestion` ile kullanıcıya göster, onay al:
- Seçenek 1: Önerilen mesaj (label olarak mesajı göster)
- Seçenek 2: "Düzenleyeyim" — kullanıcı Other ile kendi mesajını yazar

### 3. Stage + Commit

`logs/` altındaki dosyaları **stage'e EKLEME** — runtime artifact.
Sadece kaynak kodu ve config dosyalarını stage'e al:

```bash
git add --all -- ':!logs/'
```

Commit:
```bash
git commit -m "<mesaj>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

### 4. Push

```bash
git push -u origin <branch>
```

Push başarısızsa hata mesajını kullanıcıya göster, dur.

### 5. PR oluştur

`git log main..HEAD --oneline` ile branch'teki tüm commit'leri listele.
Değişikliklerin özeti + motivasyonundan PR body'si üret:

```bash
gh pr create \
  --title "<commit mesajıyla aynı veya daha kısa>" \
  --body "$(cat <<'EOF'
## Özet
- <madde 1>
- <madde 2>

## Test
- [ ] Dry-run ile test edildi
- [ ] Import hatası yok

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

PR URL'ini kullanıcıya göster.

## Notlar

- `logs/` dosyaları commit'e girmesin — pipeline checkpoint, PID lock, health JSON runtime artifact.
- Force push yapma.
- PR zaten varsa `gh pr create` hata verir — `gh pr view` ile URL'i göster.

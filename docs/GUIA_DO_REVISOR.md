# Guia do Revisor · LightNovelsCafe

Este guia é para quem vai revisar traduções das light novels. O objetivo da revisão é garantir **leitura fluida em português brasileiro**, fidelidade ao original e consistência rigorosa com a lore e o glossário da Type-Moon.

---

## 1. Como funciona a estrutura de um volume

Cada volume fica em `obras/<obra>/vol-<n>/`:

```
obras/fate-apocrypha/vol-1/
├── README.md                   -> Informações do volume e comandos rápidos
├── NOTAS_DE_ADAPTACAO.md       -> Vozes de personagens e decisões de tradução
├── REVISAO.md                  -> Checklist de revisão por arquivo
├── manifest.json               -> Mapa técnico do volume (não edite à mão)
├── package/                    -> EPUB descompactado (atualizado automaticamente)
└── translation/
    ├── translated-html/        -> ✏️ ONDE VOCÊ EDITA! Arquivos da tradução
    ├── original-html/          -> Original intocado (para referência)
    └── plaintext/              -> Original em texto puro
```

> **Regra de ouro:** Você só precisa editar os arquivos dentro de `translation/translated-html/` e marcar seu progresso em `REVISAO.md`. Nunca edite os arquivos de `package/` ou `original-html/` diretamente.

---

## 2. Passo a passo para revisar um capítulo

### Passo 1: Abra a comparação visual no navegador
Rode no terminal da raiz do projeto:

```bash
.\lnc comparar fate-apocrypha/1 --abrir
```
*(No Linux ou Mac: `./lnc comparar fate-apocrypha/1 --abrir`)*

Isso vai abrir uma página HTML com duas colunas lado a lado:
- **Coluna da esquerda:** texto original.
- **Coluna da direita:** tradução em português.
- Linhas em **amarelo** mostram trechos que ainda estão idênticos ao original (possivelmente esquecidos sem tradução).

Se quiser comparar só um arquivo específico (por exemplo, o arquivo 5):
```bash
.\lnc comparar fate-apocrypha/1 --arquivo 5 --abrir
```

### Passo 2: Edite a tradução
Abra o arquivo correspondente em `obras/<obra>/vol-<n>/translation/translated-html/` no seu editor favorito (VS Code, Notepad++, etc.).

Faça os ajustes necessários no texto, **preservando as tags HTML** (como `<p>`, `<em>`, `<strong>`, `<a>`, `<span>`).

### Passo 3: Rode a validação automática
Antes de marcar como revisado, rode:

```bash
.\lnc validar fate-apocrypha/1
```

O validador confere sozinho:
- Se alguma tag HTML/XML foi quebrada;
- Se alguma imagem ou link interno sumiu;
- Se há caracteres corrompidos (`Ã©`, `â€`, `?` no meio de palavras);
- Se há falas usando travessão (`—`) em vez de aspas duplas (`" "`);
- Se há termos em inglês que possuem tradução obrigatória no glossário;
- Se restaram blocos inteiros em inglês ou caracteres japoneses soltos.

### Passo 4: Atualize o checklist de revisão
Abra o arquivo `REVISAO.md` da pasta do volume e marque com um `x` o arquivo concluído, colocando seu nome:

```markdown
- [x] `022-part0020.html` · revisor: Thiago · obs: revisado prólogo, ajustado tom da narração
```

---

## 3. Padrões editoriais obrigatórios (Type-Moon)

Todos os detalhes canônicos estão em `padroes/GLOSSARIO_TYPEMOON_OPERACIONAL.json` e `padroes/PROMPT_TRADUCAO_TYPEMOON_EPUB.md`. Os pontos essenciais que todo revisor deve saber:

### 1. Aspas duplas em diálogos (NUNCA travessão)
- **Certo:** `<p>"Eu não sei", disse Gray, abaixando a cabeça.</p>`
- **Errado:** `<p>— Eu não sei — disse Gray, abaixando a cabeça.</p>`

### 2. Classes de Servant NUNCA são traduzidas
Ficam sempre no original em inglês:
- `Saber`, `Archer`, `Lancer`, `Rider`, `Caster`, `Assassin`, `Berserker`, `Ruler`, `Avenger`, `Moon Cancer`, `Alter-Ego`, `Foreigner`, `Pretender`, `Shielder`, `Beast`.
- *Nunca use "Cavaleiro do Sabre", "Arqueiro", "Governante", etc.*

### 3. Termos centrais padronizados
| Original | Forma em PT-BR | Proibido / Evitar |
|---|---|---|
| `Magecraft` | **Magia** | Feitiçaria, Magilúdio |
| `Magic` (True Magic) | **Verdadeira Magia** | - |
| `Magic Circuit` | **Circuito Mágico** | - |
| `Magic Crest` | **Brasão Mágico** | Crista Mágica |
| `Mystic Code` | **Artefato Místico** | Código Místico |
| `Bounded Field` | **Campo Limitado** | Barreira Limitada |
| `Noble Phantasm` | **Fantasma Nobre** | Nobre Fantasma |
| `Command Spell` | **Feitiço de Comando** | Selo de Comando |
| `Clock Tower` | **Torre do Relógio** | - |
| `Holy Grail War` | **Guerra do Santo Graal** | - |

*(Consulte `padroes/GLOSSARIO_TYPEMOON_OPERACIONAL.json` para termos de cada obra).*

### 4. Voz dos personagens (Lord El-Melloi II)
- **Lord El-Melloi II (Waver):** Professor cansado, cínico, analítico, ironia amarga, focado e prolixo ao falar de magia.
- **Reines El-Melloi:** Nobreza arrogante, sarcástica, manipuladora, refinada e provocadora.
- **Gray:** Tímida, respeitosa, voz contida, chama Waver de "meu mestre".
- **Add:** Tagarela, sarcástico, agressivo, barulhento.

---

## 4. Finalizando um volume revisado

Quando todos os arquivos de um volume estiverem revisados:
1. Rode `.\lnc validar <volume>` e garanta zero erros.
2. Sincronize com o pacote do EPUB:
   ```bash
   .\lnc sincronizar <volume>
   ```
3. Teste a montagem do EPUB:
   ```bash
   .\lnc montar <volume>
   ```
4. Se estiver tudo perfeito, publique:
   ```bash
   .\lnc publicar <volume>
   ```
   Isso vai sincronizar, montar o EPUB final e atualizar a pasta `publicados/`.

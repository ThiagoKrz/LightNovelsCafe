# Guia do Tradutor · LightNovelsCafe

Este guia descreve como preparar novos volumes, fazer a triagem de termos, traduzir por etapas e compilar o EPUB final.

---

## 1. Como preparar um novo volume a partir de um EPUB

Se você tem o arquivo EPUB original de um volume que ainda não existe no projeto:

```bash
.\lnc preparar "caminho/para/livro.epub" lord-el-melloi-ii-case-files 6
```

O comando faz automaticamente:
1. Salva uma cópia segura em `originais/<obra>/vol-<n>.epub`;
2. Descompacta o EPUB completo em `obras/<obra>/vol-<n>/package/`;
3. Extrai ordenadamente cada XHTML do livro para `translation/original-html/`;
4. Cria as cópias de trabalho em `translation/translated-html/`;
5. Gera texto puro em `translation/plaintext/`;
6. Cria `manifest.json`, `README.md` e `REVISAO.md`.

---

## 2. Fluxo obrigatório de tradução

### Etapa 1: Triagem de termos antes de começar
Antes de traduzir a primeira linha:
1. Examine o sumário, nomes de personagens, feitiços, locais e itens que aparecem no volume.
2. Abra `padroes/GLOSSARIO_TYPEMOON_OPERACIONAL.json`. Se algum termo importante da lore não constar no glossário, defina a padronização e registre no arquivo antes de traduzir.
3. Se houver convenções específicas daquele volume, crie ou edite `obras/<obra>/vol-<n>/NOTAS_DE_ADAPTACAO.md`.

### Etapa 2: Tradução por etapas
- **Nunca traduza um livro inteiro de uma só vez.** Divida em lotes (ex.: Prólogo + Cap 1, depois Cap 2 + 3, etc.).
- Traduza os arquivos dentro de `translation/translated-html/`.
- **Mantenha tags HTML intactas:** `<p>`, `<span>`, `<em>`, `<a>`, referências a imagens `<img>` e quebras de linha estruturais.
- **Diálogos:** SEMPRE em aspas inglesas duplas (`" "`). Proibido usar travessão (`—`).
- **Classes de Servant:** Saber, Archer, Lancer, Rider, Caster, Assassin, Berserker, Ruler, etc., NUNCA são traduzidas.

### Etapa 3: Validação da etapa
Após traduzir um lote, rode:
```bash
.\lnc validar <obra>/<vol>
```

Corrija qualquer erro de XML, encoding ou termos crus antes de avançar para a próxima etapa.

### Etapa 4: Auditoria de consistência pós-tradução
Ao terminar todos os arquivos do volume, faça uma leitura de ponta a ponta para garantir:
- Que o tom dos personagens não oscila de um capítulo para outro;
- Que termos foram usados exatamente com a mesma grafia;
- Que não sobraram trechos em inglês nem caracteres quebrados.

### Etapa 5: Publicação
Quando o volume estiver concluído:
```bash
.\lnc publicar <obra>/<vol>
```

Esse comando:
1. Valida o volume completo;
2. Sincroniza `translated-html/` de volta para `package/`;
3. Recompila o EPUB final em `obras/<obra>/vol-<n>/build/`;
4. Copia o EPUB para `publicados/<obra>/<obra>-vol-<n>-pt-br.epub`;
5. Atualiza a tabela de status nos READMEs.

Para colocar no site do **Fate Café**, basta copiar o arquivo gerado em `publicados/` para a pasta `src/assets/<Autor>/...` do repositório do site!

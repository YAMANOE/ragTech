# Pipeline Improvements — v1.0 → v1.1
# تحسينات خط المعالجة — الإصدار 1.0 إلى 1.1

---

## Summary / ملخص

This document captures the measurable improvements made to the Jordanian Legislation
structured-data pipeline between its initial prototype (v1.0) and the current
production-ready release (v1.1).

يوثّق هذا الملف التحسينات القابلة للقياس التي أُجريت على خط معالجة البيانات المنظّمة
للتشريعات الأردنية، بين النموذج الأوّلي الأوّلي (v1.0) والإصدار الجاهز للإنتاج الحالي (v1.1).

---

## Before / After Metrics — مقارنة المقاييس

| Metric (EN) | المقياس (ع) | Before / قبل | After / بعد |
|---|---|---|---|
| Topic coverage | تغطية الموضوعات | 55.1% | **100%** |
| Entity name quality | جودة أسماء الجهات | dirty / broken | **270 clean unique** |
| Entity deduplication | إزالة تكرار الجهات | 298 names | **270 canonical** |
| Status mapping | تعيين حالة التشريع | 7 values | **20 values** |
| Paragraph splitting | تقسيم الفقرات | 5,839 sections | **8,512 sections** |
| Unknown doc_type | نوع وثيقة مجهول | 3 | **0** |
| Status mismatches | تعارض الحالات | 45 | **0** |
| Legal basis coverage | تغطية السند القانوني | partial | **38/38 = 100%** |
| Automated tests | الاختبارات الآلية | 0 | **91 / 91 passing** |
| QA checks | فحوصات الجودة | 0 | **11 / 11 passing** |

---

## Data Package — حزمة البيانات

### English

- **99 structured documents**: 96 laws · 2 regulations · 1 constitution
- **8,512 sections** with clean Arabic text and normalised Unicode
- **270 unique canonical entity names** (deduped from 298 raw names)
- **17 topic categories** — every document covered
- **38 inter-document relationships** (BASED_ON, AMENDS, REPEALS, REFERS_TO)
- **Legal basis text** extracted for all 38 subordinate laws (100%)
- **Entity roles** tracked per document: issuer, regulator, subject, enforcer
- Full relational export: `documents.csv`, `sections.csv`, `entities.csv`,
  `document_topics.csv`, `document_relationships.csv`

### العربية

- **99 وثيقة منظّمة**: 96 قانونًا · نظامان · دستور واحد
- **8,512 مقطعًا** بنص عربي نظيف وترميز يونيكود موحَّد
- **270 اسمًا فريدًا وموحَّدًا للجهات** (مُخفَّض من 298 اسمًا خامًا)
- **17 تصنيفًا موضوعيًا** — تغطية كاملة لجميع الوثائق
- **38 علاقة بين الوثائق** (مستند إلى، يُعدِّل، يُلغي، يُحيل إلى)
- **نص السند القانوني** مستخرج لجميع القوانين الاشتقاقية الـ38 (100%)
- **أدوار الجهات** موثَّقة لكل وثيقة: مُصدِر، جهة تنظيمية، موضوع، جهة تنفيذ
- تصدير علائقي كامل: documents.csv · sections.csv · entities.csv ·
  document_topics.csv · document_relationships.csv

---

## Key Technical Improvements / التحسينات التقنية الرئيسية

### 1. Arabic text normalisation — توحيد النص العربي
Systematic cleanup of hamza/alef variants (أ/إ/ا), tāʾ marbūṭa (ة/ه),
kashida removal, and Unicode zero-width characters across all section text.

تنظيف منهجي لمتغيرات الهمزة/الألف، التاء المربوطة، إزالة التطويل،
وأحرف يونيكود عديمة العرض في نص جميع الأقسام.

### 2. Entity deduplication — إزالة تكرار الجهات
29 safe merge rules applied across 37 documents; 28 duplicate name variants
collapsed to canonical forms. Risky clusters (genuinely distinct entities
with similar names) were explicitly skipped.

تطبيق 29 قاعدة دمج آمنة على 37 وثيقة؛ تقليص 28 صيغة اسم مكررة إلى أشكال
موحَّدة. تم تجاهل التجمعات المحفوفة بمخاطر (جهات مختلفة حقًا بأسماء متشابهة).

### 3. Legal basis extraction — استخراج السند القانوني
Extended `RE_LEGAL_BASIS` regex with `صادر بموجب` trigger pattern (with
lookahead guard). Gained 5 additional extractions including budget laws issued
under Article 31 of the Constitution.

توسيع نمط `RE_LEGAL_BASIS` بإضافة محفّز `صادر بموجب` (مع قيد نظرة أمامية).
اكتساب 5 عمليات استخراج إضافية بما فيها قوانين الموازنة الصادرة بموجب المادة 31
من الدستور.

### 4. Section granularity — دقة تقسيم الأقسام
Improved paragraph-splitting logic increased section count from 5,839 to
8,512 (+46%), enabling finer-grained search and cross-reference resolution.

تحسين منطق تقسيم الفقرات أدى إلى زيادة عدد الأقسام من 5,839 إلى 8,512
(بنسبة +46%)، مما يتيح بحثًا أكثر دقة وحلًا أفضل للإحالات المتقاطعة.

### 5. QA & test coverage — تغطية ضمان الجودة والاختبارات
Built full pytest suite (91 tests) and 11-point QA check covering document
counts, section integrity, entity cleanliness, topic coverage, status
consistency, and JSON↔CSV parity.

بناء مجموعة pytest كاملة (91 اختبارًا) وفحص جودة من 11 نقطة يغطي أعداد
الوثائق، سلامة الأقسام، نظافة الجهات، تغطية الموضوعات، اتساق الحالات،
والتكافؤ بين JSON وCSV.

---

*Generated: 2026-03-15 · Pipeline v1.1 · 99 documents*

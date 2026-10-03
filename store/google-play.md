# رفع Darwish Smart Power على Google Play

الملفات الجاهزة:

| الملف | استخدامه في Play Console |
|---|---|
| `darwish-smart-power.aab` (من [الإصدار app-latest](https://github.com/mohamedstar19/darwish-smart-power/releases/tag/app-latest)) | Release → Create new release → App bundles |
| `store/icon-512.png` | Store listing → App icon (512×512) |
| `store/feature-graphic.png` | Store listing → Feature graphic (1024×500) |
| https://power.darwish-tech.com/privacy | App content → Privacy policy |

صور الشاشة (Phone screenshots، من 2 لـ 8 صور): خدها من موبايلك وأنت فاتح التطبيق
(الرئيسية، الاستهلاك، المواعيد، الإعدادات).

- اسم الحزمة (Package name): `com.darwish.smartpower`. مينفعش يتغير بعد أول رفع.
- كل build من GitHub Actions بياخد versionCode أكبر من اللي قبله، فأي `.aab` جديد يترفع كتحديث على طول.

## 1. الحساب

1. https://play.google.com/console → سجّل كمطوّر. فيه رسوم مرة واحدة 25 دولار، وجوجل هتطلب منك تأكّد هويتك.
2. **حساب شخصي اتعمل بعد نوفمبر 2023:** لازم الأول تعمل **Closed testing** فيه 12 مختبِر على الأقل
   مشتركين لمدة 14 يوم ورا بعض، وبعد كده بس تقدر تطلب **Production**.
   حساب منظمة (Organization) مش عليه الشرط ده، بس محتاج رقم D-U-N-S للشركة.

## 2. إنشاء التطبيق

Create app:
- App name: `Darwish Smart Power`
- Default language: العربية (ar) — بعدين ضيف الإنجليزي (en-US) من Store listing → Manage translations
- App or game: App · Free or paid: Free
- وافق على الإقرارات.

## 3. App content (القائمة اللي على الشمال، تحت Policy)

| البند | الإجابة |
|---|---|
| Privacy policy | `https://power.darwish-tech.com/privacy` |
| Ads | No, my app does not contain ads |
| App access | All or some functionality is restricted → Add instructions (شوف تحت) |
| Content rating | الفئة: All Other App Types. كل الأسئلة (عنف، جنس، مخدرات، قمار، تواصل بين المستخدمين، مشاركة الموقع، شراء رقمي): No |
| Target audience | 18 وما فوق |
| News app | No |
| Data safety | شوف تحت |
| Government / Financial / Health | No |

### App access (عشان مراجع جوجل يقدر يدخل)

من التطبيق: الإعدادات → العائلة → أضف عضو، **صلاحية مشاهدة فقط**، اسمه `Google review`.
انسخ كود الدعوة وحطّه في Play Console بس (متبعتهوش لحد تاني):

- Username: `https://power.darwish-tech.com`
- Password: كود الدعوة
- Instructions:
  > Open the app, go to Settings → Connection. Enter the address above as the server and the code as the password, then tap Connect. This is a view-only account on a live power strip, so switching is disabled.

بعد ما التطبيق يتقبل، تقدر تمسح العضو ده.

### Data safety

- Does your app collect or share any of the required user data types? **Yes**
- Is all of the user data collected by your app encrypted in transit? **Yes**
- Do you provide a way for users to request that their data is deleted? **Yes**
- الأنواع (Data types):
  - **App activity → Other user-generated content** (أسماء المخارج، المواعيد، المشاهد)
    - Collected: Yes · Shared: No · Processed ephemerally: No
    - Required (مش اختياري)
    - Purpose: **App functionality**
  - **Device or other IDs** (رقم المشترك MAC)
    - Collected: Yes · Shared: No · Required · Purpose: **App functionality**
- مش بنجمع: الاسم، الإيميل، رقم التليفون، الموقع، جهات الاتصال، الصور، بيانات الدفع.
- إذن الموقع (أندرويد 12 وأقدم) و"الأجهزة القريبة" (أندرويد 13+) بنستخدمهم بس عشان نشوف شبكات الواي فاي القريبة ونلاقي المشترك، ومفيش أي بيانات بتطلع من الموبايل، فالموقع **مش** بيتحسب في Data safety.
- اسم وكلمة سر الواي فاي بيروحوا للمشترك مباشرة على الشبكة المحلية، مش لينا، فمش بيتحسبوا.

## 4. Store listing

### العربية (ar)

**الاسم (30 حرف):** Darwish Smart Power

**وصف قصير (80 حرف):**
تحكم في مشترك الكهرباء الذكي من موبايلك، وتابع الاستهلاك والفاتورة من أي مكان.

**الوصف الكامل:**
```
Darwish Smart Power بيخليك تتحكم في مشترك الكهرباء الذكي (4 مخارج) من موبايلك، في البيت أو برّه.

⚡ تحكم كامل
• شغّل واطفي كل مخرج لوحده، أو كذا مخرج مع بعض
• سمّي كل مخرج وحط له أيقونة، ورتّب المشتركات حسب الغرفة
• مؤقت: اطفي الجهاز بعد وقت معيّن

📊 الاستهلاك والفاتورة
• القدرة اللحظية (وات) والجهد والحرارة
• استهلاك اليوم والأسبوع والشهر بالكيلووات ساعة
• تقدير الفاتورة حسب سعر الكيلووات عندك

⏰ مواعيد ومشاهد
• مواعيد تشغيل وإطفاء يومية حسب أيام الأسبوع
• دورات: شغّل X دقيقة واطفي Y دقيقة وكرّر
• مشاهد: زرار واحد يغيّر كذا مخرج مرة واحدة

🔔 تنبيهات
• لو المشترك فصل، أو سخن، أو الحِمل زاد عن الحد

🔒 أمان
• قفل التطبيق بالبصمة أو قفل الشاشة
• رقم PIN لكل مشترك
• مشاركة مع العيلة: تحكّم أو مشاهدة بس، لمشتركات معيّنة

🗣️ أليكسا
• شغّل واطفي المخارج بصوتك من Amazon Echo

📶 حتى لو النت فصل
• الأوامر بتستنى وتتنفّذ أول ما المشترك يرجع

متوافق مع مشترك LG U+ الذكي بـ 4 مخارج وقياس استهلاك (Wi-Fi 2.4 GHz)، من غير أي تعديل على المشترك.
محتاج سيرفر Darwish Smart Power (بنركّبه لك).

تطوير Darwish Tech · darwish-tech.com
```

### English (en-US)

**Short description:**
Control your smart power strip from your phone; track energy use and your bill.

**Full description:**
```
Darwish Smart Power lets you control your 4-outlet smart power strip from your phone, at home or away.

⚡ Full control
• Switch each outlet on or off, or several at once
• Name outlets, pick icons and group strips by room
• Timers: turn a device off after a set time

📊 Energy and bill
• Live power (W), voltage and temperature
• Daily, weekly and monthly energy in kWh
• Bill estimate at your electricity price

⏰ Schedules and scenes
• Daily on/off schedules by weekday
• Cycles: on for X minutes, off for Y, repeat
• Scenes: one tap sets several outlets

🔔 Alerts
• When a strip goes offline, gets too hot, or draws too much power

🔒 Security
• App lock with fingerprint or screen lock
• PIN per strip
• Family sharing: control or view-only, for chosen strips

🗣️ Alexa
• Switch outlets by voice with an Amazon Echo

📶 Offline queue
• Commands wait and run as soon as the strip is back online

Works with the LG U+ 4-outlet smart strip with energy metering (2.4 GHz Wi-Fi), stock firmware.
Requires a Darwish Smart Power server (we install it for you).

Developed by Darwish Tech · darwish-tech.com
```

- App category: **Tools** (أو House & Home)
- Contact email: إيميل الدعم بتاعك (بيظهر للناس)
- Website: `https://power.darwish-tech.com`

## 5. أول إصدار

1. Testing → **Closed testing** → Create track → Testers: ضيف 12 إيميل جيميل على الأقل (أو Google Group).
2. Create new release → ارفع `darwish-smart-power.aab`.
3. **Play App Signing:** سيب الاختيار الافتراضي (Google يعمل مفتاح التوقيع). المفتاح بتاعنا
   (`darwish.jks`) بيبقى **مفتاح الرفع** (upload key). حافظ عليه وعلى كلمة سره، من غيرهم مش هتقدر ترفع تحديثات
   (لو ضاع، تطلب من جوجل تغييره).
   ملحوظة: نسخة Play ونسخة الموقع (`app.apk`) هيبقى توقيعهم مختلف، فاللي ينزّل من Play يكمّل تحديثات من Play،
   واللي من الموقع يكمّل من الموقع.
4. Release name: `2.0.<رقم>` · Release notes:
   ```
   <ar>أول إصدار على Google Play.</ar>
   <en-US>First release on Google Play.</en-US>
   ```
5. Review release → Start rollout. ابعت رابط الاشتراك للمختبِرين، وبعد 14 يوم: Apply for production.

## التحديثات بعد كده

أي `git push` على main ← GitHub Actions يبني `.aab` جديد برقم أعلى ← نزّله من صفحة الإصدار ← ارفعه في Release جديد.

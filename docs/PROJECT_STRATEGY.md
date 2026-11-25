# NeurixBot — stratégie des éditions et des publications

Ce document décrit la stratégie de développement à respecter pour NeurixBot. Il sert de référence aux contributeurs et aux assistants de développement (dont Claude).

## 1. Deux éditions distinctes

### NeurixBot Open Source
- Le dépôt public contient une édition utilisable et maintenue du bot.
- Les corrections de bugs et les correctifs de sécurité doivent continuer à être publiés.
- Les améliorations générales peuvent être sélectionnées et rétroportées depuis le développement privé lorsqu'elles sont compatibles et suffisamment stables.
- Cette édition ne doit pas être abandonnée pendant que l'édition privée évolue.

### NeurixBot — développement privé
- Le dépôt privé reste l'espace principal de développement de la version actuelle.
- Les nouvelles fonctionnalités peuvent y être développées et testées en exclusivité.
- Les expérimentations et fonctionnalités encore instables restent privées jusqu'à ce qu'une décision de publication soit prise.

## 2. Période d'exclusivité et publication

- La cible envisagée est une période d'exclusivité d'environ **6 mois**, ajustable selon la maturité des fonctionnalités.
- Certaines fonctionnalités pourront rester privées plus longtemps si elles ne sont pas prêtes.
- Après cette période, les fonctionnalités retenues pourront être publiées progressivement dans le dépôt Open Source.
- La durée de 6 mois est une orientation de planification, pas une date de publication automatique.

## 3. Règle non négociable : la sécurité

**Les correctifs de sécurité ne doivent jamais être réservés à l'édition privée.**

Lorsqu'un problème de sécurité affecte l'édition publique, il doit être corrigé et publié dans cette édition sans attendre la fin de la période d'exclusivité. Les corrections urgentes peuvent être publiées séparément des évolutions fonctionnelles.

## 4. Consignes de développement pour Claude et les contributeurs

Avant toute modification :
1. Identifier clairement le dépôt et l'édition concernés (public ou privé).
2. Ne pas recopier automatiquement toutes les fonctionnalités privées dans le dépôt public.
3. Ne pas supprimer ni retarder les corrections de bugs et de sécurité destinées à l'édition publique.
4. Garder les deux éditions cohérentes lorsque des correctifs communs sont nécessaires.
5. Ne publier dans l'édition publique que du code suffisamment testé, documenté et compatible avec son architecture.
6. Ne jamais inclure de secrets, tokens, fichiers `.env` ou données privées dans le dépôt public.

## 5. Principe général

Le dépôt public doit rester un projet réellement maintenu, et non une simple archive. Le développement privé permet de préparer les nouveautés ; l'édition Open Source continue de recevoir les correctifs essentiels et peut accueillir progressivement les fonctionnalités stabilisées.

En cas de doute sur le dépôt cible ou sur la publication d'une fonctionnalité, demander confirmation avant de modifier ou de publier.

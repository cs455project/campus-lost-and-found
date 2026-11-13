# TraceBack — Costing

Nov 6, 2026 · @Soumyaranjan

**Running TraceBack costs about US$24.84 (₹2,360) a month at AWS on-demand prices, and the team pays nothing: the AWS Free plan credits cover the server, and every other service is on a free tier.** The single biggest cost is the EC2 instance (78%). A one-year savings plan would cut the bill to about US$17.69 a month if the portal were kept beyond the course.

TraceBack is the IIT Kanpur Lost & Found portal. Prices are AWS on-demand list prices for the Sydney region (`ap-southeast-2`), where the server runs, in US dollars, with rupees at ₹95 = US$1 (early November 2026). A month is 730 hours.

## 1. Monthly running cost

| Item | What we use | Unit price | Per month (US$) | Per month (₹) |
| --- | --- | --- | --- | --- |
| EC2 instance | 1 × `t3.small` (2 vCPU, 2 GB), Linux, always on | $0.0264 per hour | 19.27 | 1,831 |
| Disk | 20 GB EBS gp3 | $0.096 per GB-month | 1.92 | 182 |
| Public IPv4 address | 1 Elastic IP | $0.005 per hour | 3.65 | 347 |
| Data transfer out | about 0.8 MB per page view (after thumbnails) | first 100 GB a month free | 0.00 | 0 |
| HTTPS certificate | Let’s Encrypt through Caddy | free | 0.00 | 0 |
| **Total** |  |  | **24.84** | **2,360** |

The 100 GB of free data transfer covers about 125,000 page views a month, far above campus use: at 2,000 page views a day TraceBack would send about 48 GB.

## 2. What the project actually pays

- **US$0.** The account is on the AWS Free plan, which gives US$100 in credits at sign-up and up to US$100 more for completing short console activities, for up to 6 months. At US$24.84 a month, US$100 lasts four months (November 2026 to February 2027) and US$200 the full six.
- **CPU credits:** a `t3.small` earns 20% of each core as its baseline. After the performance work a page view needs about a third of the CPU it did, so normal campus use stays inside the baseline and no surplus-credit charges ($0.05 per vCPU-hour) are expected.
- **When the course ends:** terminate the instance and release the Elastic IP, or both keep charging.

## 3. Ways to pay less

| Option | Per month (US$) | Saving | Trade-off |
| --- | --- | --- | --- |
| Current: on-demand, always on | 24.84 | – | none |
| Stop the server between demos | 5.57 (disk and IP only) | 78% | Site offline while stopped; restart takes about 2 minutes |
| 1-year EC2 Instance Savings Plan or Reserved Instance ($0.0166 per hour) | 17.69 | 29% | One-year commitment; worth it only if the portal stays in use |
| `t3.micro` (1 GB) | – | – | Not viable: the Next.js build and the AI models need 2 GB plus swap |
| Spot instance | – | – | Not suitable: AWS can stop it at any time, and the portal must stay up |

## 4. Outside services

| Service | Use | Plan | Cost |
| --- | --- | --- | --- |
| Groq (`openai/gpt-oss-20b`) | Main model for the AI matching agent | Free tier | ₹0 |
| Google Gemini (`gemini-2.5-flash`) | Fallback model and photo descriptions | Free tier | ₹0 |
| Gmail SMTP | Sign-in codes and notices | Free account with an app password | ₹0 |
| sslip.io | Domain name for the server’s IP | Free | ₹0 |
| GitHub | Repository, pull requests, reviews | Free organisation | ₹0 |
| Jira | Backlog and sprints | Free plan (the site was suspended after the team passed its member limit) | ₹0 |

The AI cost is kept at zero by design: the agent is called only when the maths is unsure (`AGENT_MATCHING=ambiguous`), and the app never makes more than 500 AI calls a day (`AI_DAILY_CALL_LIMIT`). The free tiers rate-limit rather than bill; a Groq rate-limit reply (429) was seen once, and the app then used Gemini. If the same limit were used every day on paid plans, it would cost about US$4.70 a month on Groq ($0.075 per million input tokens, $0.30 per million output, about 3,000 tokens in and 300 out per call) plus about US$3 for 100 Gemini photo descriptions a day ($0.30 and $2.50 per million tokens).

## 5. Development effort

For comparison, a classic size-based estimate. TraceBack’s product code is about 13,300 non-blank, non-comment lines: backend 5,760, frontend 6,650, database migrations 640, deployment scripts 220; the 6,200 lines of tests and test scripts are not counted. Basic COCOMO for an organic project gives:

```latex
E = 2.4 \times 13.3^{1.05} \approx 36\ \text{person-months}, \qquad T = 2.5 \times E^{0.38} \approx 9.8\ \text{months}
```

At a notional ₹50,000 per person-month for an entry-level developer in India, that is about ₹18.1 lakh (US$19,100). The team of six students delivered it part-time between 19 Aug and 6 Nov 2026 (11 weeks), using an AI coding assistant throughout (see the AI Engineering Log), so the estimate mostly shows how much the AI assistance and the reuse of open-source libraries saved.

## Sources

- [AWS EC2 t3.small price in ap-southeast-2](https://calculator.holori.com/aws/ec2/t3.small/ap-southeast-2) and [regional price list](https://aws-pricing.com/ap-southeast-2.html) (EBS gp3 $0.096 per GB-month), checked 6 Nov 2026
- [Amazon EC2 T3 instances](https://aws.amazon.com/ec2/instance-types/t3/) (20% baseline, $0.05 per vCPU-hour surplus credits)
- [AWS Free Tier data transfer: 100 GB a month](https://aws.amazon.com/blogs/aws/aws-free-tier-data-transfer-expansion-100-gb-from-regions-and-1-tb-from-amazon-cloudfront-per-month)
- [Groq gpt-oss-20b prices](https://computeprices.com/providers/groq/models/gpt-oss-20b) and [Gemini 2.5 Flash prices](https://www.kunalganglani.com/llm-prices/gemini-2.5-flash)
- Exchange rate: [cross-rate from the Nepal Rastra Bank figures, 2 Nov 2026](https://ekantipur.com/business/2026/11/02/en/november-2-2026-whats-the-exchange-rate.html)

"""Update data.json with real Toss API data and market news"""
import asyncio, os, json
from dotenv import load_dotenv
load_dotenv()
from backend.api.toss_client import TossClient, TossApiError
from backend.teams.portfolio_team import PortfolioTeam
from backend.models.schemas import TossApiConfig

def get_news_mock(keywords):
    return [
        {'title':'AI semiconductor demand surge','summary':'Big tech firms increase AI chip orders','url':'https://example.com/news1','datetime':'2026-10-18 07:00:00','source':'Example News','sentiment':{'positive':0.8}}
    ]

from backend.models.schemas import MarketTrendData, NewsData

async def main():
    # Toss config
    toss_config = TossApiConfig(
        api_key=os.getenv('TOSS_API_KEY'),
        base_url='https://api.tossinvest.com/v1',
        timeout=30,
        paper_trading=False
    )
    # Fetch portfolio
    portfolio_team = PortfolioTeam(toss_config)
    try:
        portfolio = await portfolio_team.sync_portfolio()
    except Exception as e:
        print('Toss API error, fallback to simulation:', e)
        portfolio = portfolio_team._simulate_portfolio()
    await portfolio_team.close()

    # Fetch market news
    news = get_news_mock(['AI', 'semiconductor', 'earnings'])
    # Attach first news URL to each top pick for easy rendering
    if news:
        first_news_url = news[0].get('url')
        first_news_source = news[0].get('source', '출처')
    else:
        first_news_url = ''
        first_news_source = ''
    # Load existing data
    data_path = 'data.json'
    if os.path.exists(data_path):
        with open(data_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = {}
    # Ensure top_picks have news_url and news_source fields
    for pick in data.get('top_picks', []):
        pick['news_url'] = first_news_url
        pick['news_source'] = first_news_source
    data['portfolio'] = portfolio.model_dump() if hasattr(portfolio, 'model_dump') else (portfolio.dict() if hasattr(portfolio, 'dict') else portfolio)
    data['news'] = news
    # Save back
    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    # Load existing data
    data_path = 'data.json'
    if os.path.exists(data_path):
        with open(data_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = {}
    data['portfolio'] = portfolio.model_dump() if hasattr(portfolio, 'model_dump') else (portfolio.dict() if hasattr(portfolio, 'dict') else portfolio)
    data['news'] = news
    # Save back
    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

if __name__ == '__main__':
    asyncio.run(main())

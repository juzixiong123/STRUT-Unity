int classify_score(int score)
{
    if (score < 0) {
        return -1;
    }
    if (score >= 60) {
        return 1;
    }
    return 0;
}

